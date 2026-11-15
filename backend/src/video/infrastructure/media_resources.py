"""Nonsecret media limits and effective Linux container CPU/cgroup budgets."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class MediaResourceError(RuntimeError):
    """Safe stable reason; never includes input paths, commands or process output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    @property
    def retryable(self) -> bool:
        return self.code in {"workspace_unavailable", "media_start_failed", "media_exit_failed"}


@dataclass(frozen=True, slots=True)
class MediaLimits:
    workspace_bytes: int = 32 * 1024**3
    reserve_bytes: int = 2 * 1024**3
    idle_seconds: int = 30 * 60
    attempt_seconds: int = 7 * 24 * 60 * 60

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.workspace_bytes,
                self.reserve_bytes,
                self.idle_seconds,
                self.attempt_seconds,
            )
        ):
            raise MediaResourceError("invalid_media_limits")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> MediaLimits:
        values = os.environ if environment is None else environment
        defaults = cls()
        try:
            return cls(
                **{
                    field: int(values.get(key, str(getattr(defaults, field))))
                    for field, key in (
                        ("workspace_bytes", "MEDIA_WORKSPACE_BYTES"),
                        ("reserve_bytes", "MEDIA_RESERVE_BYTES"),
                        ("idle_seconds", "MEDIA_IDLE_SECONDS"),
                        ("attempt_seconds", "MEDIA_ATTEMPT_SECONDS"),
                    )
                }
            )
        except ValueError:
            raise MediaResourceError("invalid_media_limits") from None


@dataclass(frozen=True, slots=True)
class CpuBudget:
    capacity: Fraction

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise MediaResourceError("invalid_cpu_budget")

    @property
    def pool_size(self) -> int:
        return max(1, int(self.capacity))

    @property
    def svt_level(self) -> int:
        return 1 if self.capacity < 2 else 2

    @classmethod
    def detect(cls) -> CpuBudget:
        try:
            capacity = Fraction(len(os.sched_getaffinity(0)))
            for directory, version in cgroup_directories("cpu"):
                if version == 2:
                    path = directory / "cpu.max"
                    if not path.exists():
                        continue  # CPU controller may be disabled at this level.
                    quota, period = path.read_text().split()
                    if quota == "max":
                        continue
                else:
                    quota = (directory / "cpu.cfs_quota_us").read_text().strip()
                    period = (directory / "cpu.cfs_period_us").read_text().strip()
                    if quota == "-1":
                        continue
                limit = Fraction(int(quota), int(period))
                if limit <= 0:
                    raise ValueError("Invalid CPU quota")
                capacity = min(capacity, limit)
            return cls(capacity)
        except OSError, ValueError, ZeroDivisionError:
            raise MediaResourceError("cpu_budget_unavailable") from None


def _unescape_mount(value: str) -> str:
    for encoded, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(encoded, decoded)
    return value


def cgroup_directories(
    controller: str,
    *,
    membership: Path = Path("/proc/self/cgroup"),
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> tuple[tuple[Path, int], ...]:
    """Resolve own cgroup plus visible ancestors, for v2 or v1 controller mounts.

    Namespace mount roots may hide stricter ancestors. The kernel still enforces
    those; only limits visible to this container can inform pool sizing.
    """
    memberships: list[tuple[str, int]] = []
    for line in membership.read_text().splitlines():
        hierarchy, controllers, group = line.split(":", 2)
        if hierarchy == "0" and not controllers:
            memberships.append((group, 2))
        elif controller in controllers.split(","):
            memberships.append((group, 1))
    found: list[tuple[Path, int]] = []
    for line in mountinfo.read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields, properties = left.split(), right.split()
        filesystem, super_options = properties[0], properties[2].split(",")
        for group, version in memberships:
            if filesystem != ("cgroup2" if version == 2 else "cgroup"):
                continue
            if version == 1 and controller not in super_options:
                continue
            root, mount = Path(_unescape_mount(fields[3])), Path(_unescape_mount(fields[4]))
            try:
                relative = Path(group).relative_to(root)
            except ValueError:
                continue
            current = mount / relative
            if ".." in relative.parts:
                raise ValueError("Invalid cgroup membership")
            while True:
                entry = current, version
                if entry not in found:
                    found.append(entry)
                if current == mount:
                    break
                current = current.parent
    if memberships and not found:
        raise ValueError("Cgroup mount for controller is not visible")
    return tuple(found)


def memory_oom_kills() -> int:
    """Counters are advisory: a killed worker itself is handled by job recovery."""
    try:
        for directory, version in cgroup_directories("memory"):
            path = directory / ("memory.events" if version == 2 else "memory.oom_control")
            if path.exists():
                values = dict(line.split() for line in path.read_text().splitlines())
                return int(values.get("oom_kill", "0"))
    except OSError, ValueError:
        pass
    return 0
