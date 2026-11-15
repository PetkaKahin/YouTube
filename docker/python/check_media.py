"""Exercise CPU-only Vulkan, actual color conversion and AV1 in a Docker image."""

import json
import os
import resource
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest import TestCase


def main() -> None:
    started = perf_counter()
    check = TestCase()
    check.assertNotEqual(os.geteuid(), 0, "Run the media check as the image's app user.")
    icd = "/usr/share/vulkan/icd.d/lvp_icd.json"
    check.assertEqual(os.environ.get("VK_DRIVER_FILES"), icd)
    check.assertTrue(Path(icd).is_file(), "Lavapipe ICD is missing.")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise SystemExit("FFmpeg and ffprobe are required.")

    # Bound this tiny qualification probe independently of future worker policy.
    environment = {**os.environ, "LP_NUM_THREADS": "2"}

    def invoke(arguments: list[str], *, expect_success: bool = True, env=None):
        result = subprocess.run(  # noqa: S603 -- fixed tools/filters; paths are temporary test files
            arguments,
            env=environment if env is None else env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if expect_success:
            check.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-4000:])
        return result

    device_probe = invoke(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "verbose",
            "-init_hw_device",
            "vulkan=cpu:0",
            "-filter_hw_device",
            "cpu",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=64x64:rate=1:duration=1",
            "-vf",
            "libplacebo=format=yuv420p10le",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ]
    )
    device_log = device_probe.stderr.decode(errors="replace").lower()
    check.assertTrue("llvmpipe" in device_log or "lavapipe" in device_log, device_log)

    with TemporaryDirectory(prefix="youtube-media-") as work:
        directory = Path(work)
        # A missing forced driver must fail instead of selecting some host GPU.
        failed_output = directory / "must-not-exist.mp4"
        failed = invoke(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-init_hw_device",
                "vulkan=cpu:0",
                "-f",
                "lavfi",
                "-i",
                "color=black:size=64x64:rate=1:duration=1",
                "-frames:v",
                "1",
                "-c:v",
                "libsvtav1",
                str(failed_output),
            ],
            expect_success=False,
            env={**environment, "VK_DRIVER_FILES": str(directory / "missing-icd.json")},
        )
        check.assertNotEqual(failed.returncode, 0)
        check.assertFalse(failed_output.exists())

        cases = 0
        for transfer in ("smpte2084", "arib-std-b67"):
            source = (
                "testsrc2=size=64x64:rate=2:duration=1,format=yuv420p10le,"
                f"setparams=color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc"
            )
            prefix = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-init_hw_device",
                "vulkan=cpu:0",
                "-filter_hw_device",
                "cpu",
                "-filter_threads",
                "1",
                "-f",
                "lavfi",
                "-i",
                source,
            ]
            raw_options = [
                "-frames:v",
                "2",
                "-pix_fmt",
                "yuv420p10le",
                "-c:v",
                "rawvideo",
                "-threads:v",
                "1",
                "-f",
                "rawvideo",
                "-",
            ]
            original = invoke([*prefix, *raw_options]).stdout
            check.assertEqual(len(original), 2 * 64 * 64 * 3)
            for target in ("sdr", "hdr10"):
                primaries, trc, matrix = (
                    ("bt709", "bt709", "bt709")
                    if target == "sdr"
                    else ("bt2020", "smpte2084", "bt2020nc")
                )
                color_filter = (
                    f"libplacebo=format=yuv420p10le:color_primaries={primaries}:"
                    f"color_trc={trc}:colorspace={matrix}:tonemapping=bt.2390:"
                    "apply_dolbyvision=true"
                )
                rendered = invoke([*prefix, "-vf", color_filter, *raw_options]).stdout
                check.assertEqual(len(rendered), len(original))
                if target == "sdr" or transfer == "arib-std-b67":
                    check.assertNotEqual(rendered, original, "Pixels were only retagged.")

                output = directory / f"{transfer}-{target}.mp4"
                invoke(
                    [
                        *prefix,
                        "-vf",
                        color_filter,
                        "-frames:v",
                        "2",
                        "-an",
                        "-c:v",
                        "libsvtav1",
                        "-preset",
                        "6",
                        "-crf",
                        "28",
                        "-svtav1-params",
                        "tune=0:lp=2",
                        str(output),
                    ]
                )
                probe = invoke(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-count_frames",
                        "-show_entries",
                        "stream=codec_name,width,height,pix_fmt,color_space,color_transfer,"
                        "color_primaries,nb_read_frames",
                        "-of",
                        "json",
                        str(output),
                    ]
                )
                streams = json.loads(probe.stdout)["streams"]
                check.assertEqual(len(streams), 1)
                stream = streams[0]
                for key, expected in {
                    "codec_name": "av1",
                    "width": 64,
                    "height": 64,
                    "pix_fmt": "yuv420p10le",
                    "color_space": matrix,
                    "color_transfer": trc,
                    "color_primaries": primaries,
                    "nb_read_frames": "2",
                }.items():
                    check.assertEqual(stream[key], expected, (key, stream))
                cases += 1

    print(
        f"Media check passed: app user, Lavapipe CPU, missing-driver refusal, "
        f"{cases} PQ/HLG to SDR/HDR10 AV1 cases, decoded frames and pixel conversion."
    )
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(
        f"Synthetic probe only: {perf_counter() - started:.2f}s elapsed, "
        f"{usage.ru_utime + usage.ru_stime:.2f}s child CPU, "
        f"{usage.ru_maxrss / 1024:.1f} MiB largest child RSS."
    )


if __name__ == "__main__":
    main()
