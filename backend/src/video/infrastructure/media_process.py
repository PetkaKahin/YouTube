"""Supervise a trusted media command without shell execution or raw-output logging."""

import asyncio
import contextlib
import os
import shutil
import signal
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from video.infrastructure.media_resources import MediaLimits, MediaResourceError, memory_oom_kills


def check_workspace(root: Path, limits: MediaLimits) -> None:
    """Count regular bytes without following links; never delete input or output."""
    try:
        if root.is_symlink() or not root.is_dir():
            raise MediaResourceError("invalid_workspace")
        pending, size = [root], 0
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # FFmpeg may atomically rename a completed segment.
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(metadata.st_mode):
                        size += metadata.st_size
                        if size > limits.workspace_bytes:
                            raise MediaResourceError("workspace_limit")
                    else:
                        raise MediaResourceError("invalid_workspace_entry")
        if shutil.disk_usage(root).free < limits.reserve_bytes:
            raise MediaResourceError("disk_reserve")
    except OSError:
        raise MediaResourceError("workspace_unavailable") from None


@dataclass(frozen=True, slots=True)
class GuardTiming:
    poll_seconds: float = 1
    terminate_seconds: float = 5

    def __post_init__(self) -> None:
        if not 0 < self.poll_seconds <= 1 or not 0 < self.terminate_seconds <= 5:
            raise ValueError("Invalid supervisor timing")


class _Progress:
    def __init__(self) -> None:
        self.changed = monotonic()
        self.maximum: dict[bytes, int] = {}

    def feed(self, line: bytes) -> None:
        key, separator, value = line.partition(b"=")
        if separator and key in {b"out_time_us", b"frame", b"total_size"}:
            value = value.strip()
            if value.isdigit() and len(value) < 24:
                number = int(value)
                if number > self.maximum.get(key, 0):
                    self.maximum[key] = number
                    self.changed = monotonic()


async def _drain(reader: asyncio.StreamReader, progress: _Progress | None = None) -> bool:
    pending = b""
    dropping = False
    nonempty = False
    while chunk := await reader.read(8192):
        nonempty = nonempty or bool(chunk.strip())
        if progress is None:
            continue  # Logs may contain private input paths; discard in bounded chunks.
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                if not dropping:
                    progress.feed(pending)
                pending, dropping = b"", False
            if len(pending) + len(part) > 8192:
                pending, dropping = b"", True
            elif not dropping:
                pending += part
    if pending and not dropping and progress is not None:
        progress.feed(pending)
    return nonempty


def _signal_group(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, sig)


async def _stop_group(process: asyncio.subprocess.Process, timing: GuardTiming) -> None:
    _signal_group(process.pid, signal.SIGTERM)
    until = monotonic() + timing.terminate_seconds
    while monotonic() < until:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(min(0.05, timing.terminate_seconds))
    # Kill descendants even when the group leader has already exited.
    _signal_group(process.pid, signal.SIGKILL)
    await process.wait()


async def _finish_cleanup(task: asyncio.Task[None]) -> None:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def run_media_process(
    executable: Path,
    arguments: Sequence[str],
    *,
    workspace: Path,
    limits: MediaLimits,
    environment: Mapping[str, str] | None = None,
    timing: GuardTiming | None = None,
    pass_fds: tuple[int, ...] = (),
    reject_stderr: bool = False,
) -> None:
    """Run server-constructed argv with FFmpeg -progress pipe:1 output.

    This adapter neither publishes nor removes media. Resource failures are
    stable codes for future job journaling/nonretry policy. It must be called
    in the dedicated Docker CPU/RAM cgroup, not inside the API container.
    """
    timing = timing or GuardTiming()
    if not executable.is_absolute() or not executable.is_file():
        raise MediaResourceError("invalid_media_executable")
    await asyncio.to_thread(check_workspace, workspace, limits)
    started, baseline_oom = monotonic(), memory_oom_kills()
    progress = _Progress()
    # Shield creation so cancellation during fork/exec cannot leak a child.
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            str(executable),
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=environment,
            start_new_session=True,
            limit=16384,
            pass_fds=pass_fds,
        )
    )
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:

        async def stop_spawned() -> None:
            try:
                created = await spawn
            except OSError:
                return
            drains = [
                _drain(reader) for reader in (created.stdout, created.stderr) if reader is not None
            ]
            await asyncio.gather(_stop_group(created, timing), *drains)

        await _finish_cleanup(asyncio.create_task(stop_spawned()))
        raise
    except OSError:
        raise MediaResourceError("media_start_failed") from None
    if process.stdout is None or process.stderr is None:
        await _stop_group(process, timing)
        raise MediaResourceError("media_pipe_failed")
    stdout = asyncio.create_task(_drain(process.stdout, progress))
    stderr = asyncio.create_task(_drain(process.stderr))
    waiter = asyncio.create_task(process.wait())
    succeeded = False
    try:
        while True:
            await asyncio.wait({waiter}, timeout=timing.poll_seconds)
            await asyncio.to_thread(check_workspace, workspace, limits)
            now = monotonic()
            if now - started >= limits.attempt_seconds:
                raise MediaResourceError("attempt_timeout")
            if process.returncode is not None:
                break
            if now - progress.changed >= limits.idle_seconds:
                raise MediaResourceError("progress_timeout")
        if process.returncode != 0:
            code = "memory_limit" if memory_oom_kills() > baseline_oom else "media_exit_failed"
            raise MediaResourceError(code)
        # A successful parent must not leave other processes or pipes alive.
        _signal_group(process.pid, signal.SIGKILL)
        await asyncio.gather(stdout, stderr)
        if reject_stderr and stderr.result():
            raise MediaResourceError("media_exit_failed")
        succeeded = True
    finally:

        async def cleanup() -> None:
            if not succeeded:
                await _stop_group(process, timing)
            for task in (stdout, stderr, waiter):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout, stderr, waiter, return_exceptions=True)

        await _finish_cleanup(asyncio.create_task(cleanup()))
