"""Optional bounded synthetic measurements, not worker resource defaults."""

import argparse
import json
import os
import resource
import selectors
import shutil
import signal
import subprocess
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", required=True, choices=("1080-sdr", "2160-sdr", "2160-hdr-sdr", "2160-hdr-pq")
    )
    parser.add_argument("--lp", required=True, type=int, choices=(1, 2))
    parser.add_argument("--frames", required=True, type=int, choices=(96, 192))
    parser.add_argument("--timeout-seconds", type=int, choices=(1, 240), default=240)
    options = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit("Run as app in a fresh, resource-limited Docker container.")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise SystemExit("FFmpeg/ffprobe required.")
    cgroup = Path("/sys/fs/cgroup")
    quota, period = (cgroup / "cpu.max").read_text().split()
    if quota == "max" or (cgroup / "memory.max").read_text().strip() == "max":
        raise SystemExit("Explicit Docker CPU and RAM caps required for this benchmark.")
    cpu_quota = Fraction(int(quota), int(period))
    threads = min(len(os.sched_getaffinity(0)), max(1, int(cpu_quota)))
    hdr = "hdr" in options.case
    width, height = (1920, 1080) if options.case.startswith("1080") else (3840, 2160)
    depth = 10 if hdr else 8
    primaries, trc, matrix = (
        ("bt2020", "smpte2084", "bt2020nc") if hdr else ("bt709", "bt709", "bt709")
    )
    source = (
        f"testsrc2=size={width}x{height}:rate=60,format=yuv420p{'' if depth == 8 else '10le'},"
        f"setparams=range=limited:color_primaries={primaries}:color_trc={trc}:colorspace={matrix}"
    )
    with TemporaryDirectory(prefix="youtube-benchmark-") as work:
        directory = Path(work)
        output = directory / "video.m3u8"
        arguments = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error"]
        if hdr:
            arguments += ["-init_hw_device", "vulkan=cpu:0", "-filter_hw_device", "cpu"]
        arguments += ["-filter_threads", str(threads), "-f", "lavfi", "-i", source]
        if hdr:
            target = (
                ("bt709", "bt709", "bt709")
                if options.case.endswith("sdr")
                else (primaries, trc, matrix)
            )
            arguments += [
                "-vf",
                f"libplacebo=format=yuv420p10le:color_primaries={target[0]}:color_trc={target[1]}:"
                f"colorspace={target[2]}:tonemapping=bt.2390",
            ]
        arguments += [
            "-frames:v",
            str(options.frames),
            "-an",
            "-c:v",
            "libsvtav1",
            "-preset",
            "6",
            "-crf",
            "28",
            "-svtav1-params",
            f"tune=0:lp={options.lp}:irefresh-type=2",
            "-g",
            "360",
            "-progress",
            "pipe:1",
            "-stats_period",
            "1",
            "-f",
            "hls",
            "-hls_time",
            "6",
            "-hls_segment_type",
            "fmp4",
            "-hls_playlist_type",
            "vod",
            "-hls_flags",
            "independent_segments",
            str(output),
        ]
        environment = {**os.environ, "LP_NUM_THREADS": str(threads)}
        started = monotonic()
        progress_times = []
        largest_threads = 0
        timed_out = False
        with (directory / "ffmpeg.log").open("wb") as log:
            process = subprocess.Popen(  # noqa: S603 -- fixed executable/filters, synthetic input
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
                env=environment,
                start_new_session=True,
            )
            if process.stdout is None:
                raise RuntimeError("Missing progress pipe.")
            with selectors.DefaultSelector() as poller:
                poller.register(process.stdout, selectors.EVENT_READ)
                pending = b""
                last_media_time = 0
                try:
                    while process.poll() is None:
                        if monotonic() - started > options.timeout_seconds:
                            timed_out = True
                            break
                        for key, _ in poller.select(timeout=0.5):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                poller.unregister(process.stdout)
                                continue
                            pending += chunk
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                if line.startswith(b"out_time_us="):
                                    value = line.split(b"=", 1)[1]
                                    if value.isdigit() and int(value) > last_media_time:
                                        progress_times.append(monotonic() - started)
                                        last_media_time = int(value)
                        try:
                            status = Path(f"/proc/{process.pid}/status").read_text()
                        except FileNotFoundError:
                            continue
                        count = next(
                            (
                                line.split()[1]
                                for line in status.splitlines()
                                if line.startswith("Threads:")
                            ),
                            "0",
                        )
                        largest_threads = max(largest_threads, int(count))
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
                    process.stdout.close()
        elapsed = monotonic() - started
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        events = (cgroup / "memory.events").read_text().strip().splitlines()
        result = {
            "case": options.case,
            "lp_level": options.lp,
            "frames": options.frames,
            "cpu_quota": str(cpu_quota),
            "filter_threads": threads,
            "elapsed_s": round(elapsed, 3),
            "child_cpu_s": round(usage.ru_utime + usage.ru_stime, 3),
            "ffmpeg_max_rss_mib": round(usage.ru_maxrss / 1024, 1),
            "cgroup_peak_mib": round(int((cgroup / "memory.peak").read_text()) / (1024**2), 1),
            "observed_ffmpeg_threads": largest_threads,
            "first_media_progress_s": round(progress_times[0], 3) if progress_times else None,
            "max_progress_gap_s": round(
                max(
                    (
                        right - left
                        for left, right in zip(
                            [0, *progress_times], [*progress_times, elapsed], strict=True
                        )
                    ),
                    default=elapsed,
                ),
                3,
            ),
            "output_bytes": sum(
                path.stat().st_size
                for path in directory.iterdir()
                if path.suffix in {".m3u8", ".m4s", ".mp4"}
            ),
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "memory_events": dict(line.split() for line in events),
        }
        if process.returncode == 0 and not timed_out:
            decoded = subprocess.run(  # noqa: S603 -- generated local output, fixed ffprobe
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-threads",
                    "1",
                    "-protocol_whitelist",
                    "file",
                    "-count_frames",
                    "-show_entries",
                    "stream=codec_name,nb_read_frames,pix_fmt,width,height",
                    "-of",
                    "json",
                    str(output),
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
            streams = (
                json.loads(decoded.stdout).get("streams", []) if decoded.returncode == 0 else []
            )
            result["decoded"] = bool(
                len(streams) == 1
                and streams[0].get("nb_read_frames") == str(options.frames)
                and streams[0].get("codec_name") == "av1"
                and streams[0].get("width") == width
                and streams[0].get("height") == height
                and streams[0].get("pix_fmt") == ("yuv420p" if depth == 8 else "yuv420p10le")
            )
        else:
            result["decoded"] = False
            result["error_tail"] = (directory / "ffmpeg.log").read_text(errors="replace")[-1200:]
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result["decoded"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
