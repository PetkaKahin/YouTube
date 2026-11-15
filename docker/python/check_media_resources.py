"""Exercise the production resource guard in the dedicated Compose media cgroup."""

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from video.infrastructure.media_process import run_media_process
from video.infrastructure.media_resources import CpuBudget, MediaLimits, MediaResourceError


async def check(expect_oom: bool) -> None:
    assertions = TestCase()
    assertions.assertNotEqual(os.geteuid(), 0)
    budget = CpuBudget.detect()
    limits = MediaLimits.from_environment()
    scratch = Path("/var/lib/youtube/media")
    assertions.assertTrue(scratch.is_dir())
    assertions.assertFalse(scratch.is_symlink())
    # Docker enforces RAM/swap, rather than a process address-space rlimit that
    # can conflict with the encoder's virtual memory mappings.
    cgroup = Path("/sys/fs/cgroup")
    actual_memory = int((cgroup / "memory.max").read_text())
    actual_swap = int((cgroup / "memory.swap.max").read_text())
    assertions.assertEqual(actual_swap, 0)
    assertions.assertGreater(actual_memory, 0)
    with TemporaryDirectory(prefix="qualification-", dir=scratch) as work:
        directory = Path(work)
        original, ready = directory / "original", directory / "ready"
        original.write_bytes(b"must remain for retries")
        ready.write_bytes(b"previous successful quality")
        if expect_oom:
            try:
                await run_media_process(
                    Path(sys.executable),
                    [
                        "-c",
                        "import time; blocks=[]\nwhile True:\n"
                        " blocks.append(bytearray(16*1024**2)); time.sleep(.01)",
                    ],
                    workspace=directory,
                    limits=limits,
                )
            except MediaResourceError as error:
                assertions.assertEqual(error.code, "memory_limit")
            else:
                raise AssertionError("Expected the actual container RAM cap to stop the child.")
        else:
            ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
            if ffmpeg is None or ffprobe is None:
                raise AssertionError("FFmpeg and ffprobe are required.")
            output = directory / "output $(touch sentinel).mp4"
            await run_media_process(
                Path(ffmpeg),
                [
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-filter_threads",
                    str(budget.pool_size),
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=64x64:rate=24:duration=0.5",
                    "-frames:v",
                    "12",
                    "-an",
                    "-c:v",
                    "libsvtav1",
                    "-preset",
                    "6",
                    "-crf",
                    "28",
                    "-svtav1-params",
                    f"tune=0:lp={budget.svt_level}",
                    "-progress",
                    "pipe:1",
                    str(output),
                ],
                workspace=directory,
                limits=limits,
                environment={**os.environ, "LP_NUM_THREADS": str(budget.pool_size)},
            )
            probe = await asyncio.create_subprocess_exec(
                ffprobe,
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                "stream=codec_name,nb_read_frames",
                "-of",
                "json",
                str(output),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(probe.communicate(), timeout=30)
            assertions.assertEqual(probe.returncode, 0, stderr.decode(errors="replace"))
            stream = json.loads(stdout)["streams"][0]
            assertions.assertEqual(stream["codec_name"], "av1")
            assertions.assertEqual(stream["nb_read_frames"], "12")
            assertions.assertFalse((directory / "sentinel").exists())
        assertions.assertEqual(original.read_bytes(), b"must remain for retries")
        assertions.assertEqual(ready.read_bytes(), b"previous successful quality")
    print(
        "Media resource check passed: "
        f"CPU={budget.capacity}, pools={budget.pool_size}, SVT level={budget.svt_level}, "
        f"RAM={actual_memory}, swap=0, "
        f"{'real cgroup OOM recognized' if expect_oom else '12 AV1 frames encoded/decoded'}, "
        "original/ready unchanged, no shell or secrets."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-oom", action="store_true")
    asyncio.run(check(parser.parse_args().expect_oom))
