"""Container budgets, bounded progress and actual process-group termination."""

import asyncio
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import pytest

from video.infrastructure.media_process import GuardTiming, check_workspace, run_media_process
from video.infrastructure.media_resources import (
    CpuBudget,
    MediaLimits,
    MediaResourceError,
    cgroup_directories,
)

LIMITS = MediaLimits(workspace_bytes=1024**2, reserve_bytes=1, idle_seconds=2, attempt_seconds=5)
TIMING = GuardTiming(poll_seconds=0.02, terminate_seconds=0.05)


def run_script(workspace, code, limits=LIMITS):
    return run_media_process(
        Path(sys.executable), ["-c", code], workspace=workspace, limits=limits, timing=TIMING
    )


@pytest.mark.parametrize(
    "capacity,pool,level",
    [(Fraction(1, 2), 1, 1), (Fraction(3, 2), 1, 1), (Fraction(2), 2, 2), (Fraction(16), 16, 2)],
)
def test_cpu_policy_uses_quota_instead_of_visible_host_cores(capacity, pool, level):
    budget = CpuBudget(capacity)
    assert (budget.pool_size, budget.svt_level) == (pool, level)


@pytest.mark.parametrize("version", [1, 2])
def test_cgroup_resolution_walks_ancestors_with_namespace_mount_root(tmp_path, version):
    mount = tmp_path / "cgroup"
    group = mount / "parent" / "worker"
    group.mkdir(parents=True)
    membership, info = tmp_path / "membership", tmp_path / "mountinfo"
    if version == 2:
        membership.write_text("0::/namespace/parent/worker\n")
        info.write_text(f"1 0 0:1 /namespace {mount} rw - cgroup2 cgroup rw\n")
        (group / "cpu.max").write_text("max 100000\n")
        (group.parent / "cpu.max").write_text("150000 100000\n")
        (mount / "cpu.max").write_text("200000 100000\n")
    else:
        membership.write_text("4:cpu,cpuacct:/namespace/parent/worker\n")
        info.write_text(f"1 0 0:1 /namespace {mount} rw - cgroup cgroup rw,cpu,cpuacct\n")
        for directory, quota in ((group, -1), (group.parent, 150000), (mount, 200000)):
            (directory / "cpu.cfs_quota_us").write_text(str(quota))
            (directory / "cpu.cfs_period_us").write_text("100000")
    directories = cgroup_directories("cpu", membership=membership, mountinfo=info)
    assert directories == ((group, version), (group.parent, version), (mount, version))
    with (
        patch("video.infrastructure.media_resources.cgroup_directories", return_value=directories),
        patch("os.sched_getaffinity", return_value=set(range(16))),
    ):
        assert CpuBudget.detect().capacity == Fraction(3, 2)
    with (
        patch("video.infrastructure.media_resources.cgroup_directories", return_value=directories),
        patch("os.sched_getaffinity", return_value={0}),
    ):
        assert CpuBudget.detect().capacity == 1


def test_malformed_quota_fails_instead_of_using_unrestricted_host_cpu_count(tmp_path):
    (tmp_path / "cpu.max").write_text("200000 0")
    with patch(
        "video.infrastructure.media_resources.cgroup_directories", return_value=((tmp_path, 2),)
    ):
        with pytest.raises(MediaResourceError, match="^cpu_budget_unavailable$"):
            CpuBudget.detect()


def test_cgroup_namespace_root_and_escaped_mount_are_supported(tmp_path):
    mount = tmp_path / "with space"
    mount.mkdir()
    membership, info = tmp_path / "membership", tmp_path / "mountinfo"
    membership.write_text("0::/\n")
    encoded = str(mount).replace(" ", "\\040")
    info.write_text(f"1 0 0:1 / {encoded} rw - cgroup2 cgroup rw\n")
    assert cgroup_directories("memory", membership=membership, mountinfo=info) == ((mount, 2),)


def test_limit_configuration_defaults_and_overrides_do_not_need_secrets():
    defaults = MediaLimits.from_environment({})
    assert defaults == MediaLimits(32 * 1024**3, 2 * 1024**3, 1800, 604800)
    assert MediaLimits.from_environment({"MEDIA_IDLE_SECONDS": "17"}).idle_seconds == 17


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "1.5", "secret-do-not-echo"])
def test_invalid_configuration_is_rejected_without_echoing_values(value):
    with pytest.raises(MediaResourceError) as raised:
        MediaLimits.from_environment({"MEDIA_IDLE_SECONDS": value})
    assert str(raised.value) == "invalid_media_limits"


def test_workspace_threshold_is_inclusive_and_sparse_files_count(tmp_path):
    path = tmp_path / "sparse"
    with path.open("wb") as handle:
        handle.truncate(LIMITS.workspace_bytes)
    check_workspace(tmp_path, LIMITS)
    with path.open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(MediaResourceError, match="^workspace_limit$"):
        check_workspace(tmp_path, LIMITS)


def test_disk_reserve_is_inclusive(tmp_path):
    with patch("shutil.disk_usage", return_value=type("Usage", (), {"free": 1})()):
        check_workspace(tmp_path, LIMITS)
    with patch("shutil.disk_usage", return_value=type("Usage", (), {"free": 0})()):
        with pytest.raises(MediaResourceError, match="^disk_reserve$"):
            check_workspace(tmp_path, LIMITS)


def test_symlink_input_cannot_escape_workspace_or_get_deleted(tmp_path):
    external = tmp_path / "external"
    external.write_text("original")
    work = tmp_path / "work"
    work.mkdir()
    (work / "link").symlink_to(external)
    with pytest.raises(MediaResourceError, match="^invalid_workspace_entry$"):
        check_workspace(work, LIMITS)
    assert external.read_text() == "original"


def test_successful_process_treats_shell_characters_as_data_and_discards_private_stderr(
    tmp_path, capsys
):
    original = tmp_path / "original"
    original.write_bytes(b"keep")
    code = (
        "import sys; print('frame=1'); print('private-secret',file=sys.stderr); print(sys.argv[1])"
    )
    asyncio.run(
        run_media_process(
            Path(sys.executable),
            ["-c", code, "$(touch injected); echo hacked"],
            workspace=tmp_path,
            limits=LIMITS,
            timing=TIMING,
        )
    )
    assert original.read_bytes() == b"keep" and not (tmp_path / "injected").exists()
    assert "private-secret" not in str(capsys.readouterr())


def test_logs_without_newlines_are_drained_in_bounded_chunks(tmp_path):
    asyncio.run(
        run_script(
            tmp_path,
            "import sys; sys.stdout.write('x'*1000000); sys.stderr.write('private'*1000000)",
        )
    )


@pytest.mark.parametrize("progress", ["progress=continue", "frame=0", "frame=1"])
def test_repeated_progress_values_do_not_prevent_idle_timeout(tmp_path, progress):
    code = f"import time\nwhile True:\n print({progress!r},flush=True)\n time.sleep(.05)\n"
    with pytest.raises(MediaResourceError, match="^progress_timeout$"):
        asyncio.run(run_script(tmp_path, code, replace(LIMITS, idle_seconds=1)))


def test_advancing_progress_avoids_idle_timeout_but_not_attempt_deadline(tmp_path):
    code = (
        "import time\nfor frame in range(1000):\n"
        " print(f'frame={frame}',flush=True)\n time.sleep(.02)\n"
    )
    with pytest.raises(MediaResourceError, match="^attempt_timeout$"):
        asyncio.run(run_script(tmp_path, code, replace(LIMITS, idle_seconds=1, attempt_seconds=2)))


def test_workspace_growth_during_encoding_is_stopped_and_existing_results_are_untouched(tmp_path):
    original, ready = tmp_path / "original", tmp_path / "ready.mp4"
    original.write_bytes(b"source")
    ready.write_bytes(b"ready")
    code = "import time; open('partial','wb').write(b'x'*2000000); time.sleep(10)"
    with pytest.raises(MediaResourceError, match="^workspace_limit$"):
        asyncio.run(run_script(tmp_path, code))
    assert original.read_bytes() == b"source" and ready.read_bytes() == b"ready"


def test_fast_exit_still_checks_final_disk_usage(tmp_path):
    with pytest.raises(MediaResourceError, match="^workspace_limit$"):
        asyncio.run(run_script(tmp_path, "open('partial','wb').write(b'x'*2000000)"))


@pytest.mark.parametrize("oom,code", [(False, "media_exit_failed"), (True, "memory_limit")])
def test_exit_error_distinguishes_cgroup_oom_without_exposing_logs(tmp_path, oom, code):
    with patch("video.infrastructure.media_process.memory_oom_kills", side_effect=[0, int(oom)]):
        with pytest.raises(MediaResourceError, match=f"^{code}$"):
            asyncio.run(
                run_script(tmp_path, "import sys; print('secret',file=sys.stderr); sys.exit(1)")
            )


def _alive(pid):
    try:
        # A reaped orphan can briefly remain a zombie until Docker's init collects it.
        return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def test_cancel_kills_grandchild_even_if_parent_handles_term_and_exits(tmp_path):
    async def exercise():
        child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
        code = (
            "import subprocess,sys,time\n"
            f"p=subprocess.Popen([sys.executable,'-c',{child!r}])\n"
            "open('grandchild.pid','w').write(str(p.pid))\n"
            "time.sleep(60)\n"
        )
        task = asyncio.create_task(run_script(tmp_path, code))
        async with asyncio.timeout(3):
            while not (tmp_path / "grandchild.pid").exists():
                await asyncio.sleep(0.01)
        pid = int((tmp_path / "grandchild.pid").read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not _alive(pid)

    asyncio.run(exercise())


def test_timeout_escalates_for_process_ignoring_sigterm(tmp_path):
    code = (
        "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "open('pid','w').write(str(os.getpid())); time.sleep(60)"
    )
    with pytest.raises(MediaResourceError, match="^progress_timeout$"):
        asyncio.run(run_script(tmp_path, code, replace(LIMITS, idle_seconds=1)))
    assert not _alive(int((tmp_path / "pid").read_text()))


def test_preflight_refusal_does_not_start_command(tmp_path):
    (tmp_path / "too-big").write_bytes(b"x" * 2000000)
    with pytest.raises(MediaResourceError, match="^workspace_limit$"):
        asyncio.run(run_script(tmp_path, "open('started','w').write('bad')"))
    assert not (tmp_path / "started").exists()


@pytest.mark.parametrize(
    "code",
    ["workspace_limit", "disk_reserve", "memory_limit", "attempt_timeout", "progress_timeout"],
)
def test_repeating_the_same_resource_budget_is_not_recommended(code):
    assert not MediaResourceError(code).retryable
    assert MediaResourceError("workspace_unavailable").retryable


def test_normal_parent_exit_cannot_leave_running_descendants_with_open_pipes(tmp_path):
    code = (
        "import subprocess,sys\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "open('descendant','w').write(str(p.pid))\n"
    )
    asyncio.run(run_script(tmp_path, code))
    assert not _alive(int((tmp_path / "descendant").read_text()))


def test_cancellation_during_spawn_cannot_leak_the_created_process(tmp_path):
    async def exercise():
        real_spawn = asyncio.create_subprocess_exec
        created = asyncio.Event()

        async def delayed_spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            created.set()
            await asyncio.sleep(0.05)
            return process

        with patch("asyncio.create_subprocess_exec", side_effect=delayed_spawn):
            task = asyncio.create_task(
                run_script(
                    tmp_path,
                    "import os,time; open('spawned','w').write(str(os.getpid())); time.sleep(60)",
                )
            )
            async with asyncio.timeout(3):
                await created.wait()
                while not (tmp_path / "spawned").exists():
                    await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not _alive(int((tmp_path / "spawned").read_text()))

    asyncio.run(exercise())


def test_repeated_cancellation_cannot_interrupt_process_group_cleanup(tmp_path):
    async def exercise():
        code = (
            "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "open('pid','w').write(str(os.getpid())); time.sleep(60)"
        )
        task = asyncio.create_task(run_script(tmp_path, code))
        async with asyncio.timeout(3):
            while not (tmp_path / "pid").exists():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not _alive(int((tmp_path / "pid").read_text()))

    asyncio.run(exercise())
