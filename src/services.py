import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from src.values import GITHUB_TOKEN

logger = logging.getLogger(__name__)

SERVICES_CACHE_TTL_SECONDS = 5.0
CI_STATUS_CACHE_TTL_SECONDS = 60.0
DU_TIMEOUT_SECONDS = 60
SYSTEMD_UNSET = 2**64 - 1  # value systemd reports for an unset uint64 property (e.g. MemoryCurrent=[not set])
_services_cache: tuple[float, list[str]] | None = None
_ci_status_cache: dict[str, tuple[float, str | None]] = {}


@dataclass
class ServiceStatus:
    name: str
    is_active: bool
    is_failed: bool
    uptime: str | None
    memory: str | None
    cpu: str | None
    last_error: str | None
    full_status: str
    project_group: str
    suffix: str | None
    ci_status: str | None

    @property
    def is_timer(self) -> bool:
        return self.name.endswith(".timer")

    @property
    def display_name(self) -> str:
        """Unit name without the `projects_` prefix or the unit-type extension."""
        stem = self.name.removeprefix("projects_")
        for extension in (".service", ".timer"):
            stem = stem.removesuffix(extension)
        return stem


def parse_service_name(service_name: str) -> tuple[str, str | None]:
    """Parse unit name to extract project group and optional suffix.

    Returns:
        tuple[str, str | None]: (project_group, suffix) where suffix is None if no suffix exists.

    Examples:
        'projects_energy-monitor.service' -> ('energy-monitor', None)
        'projects_energy-monitor_data-backup-scheduler.service' -> ('energy-monitor', 'data-backup-scheduler')
        'projects_claude-usage-notch-server_ping.timer' -> ('claude-usage-notch-server', 'ping')
    """
    for suffix in (".service", ".timer"):
        if service_name.endswith(suffix):
            service_name = service_name[: -len(suffix)]
            break
    service_name = service_name.replace("projects_", "")
    parts = service_name.split("_")
    project_group = parts[0]
    suffix = "_".join(parts[1:]) if len(parts) > 1 else None
    return (project_group, suffix)


def get_ci_status(repo_name: str, use_cache: bool = True) -> str | None:
    """Get CI status from GitHub Actions API with a short TTL cache.

    Returns None when the repo or workflow is missing (e.g. self-hosted clones
    with no GitHub Actions), so the dashboard omits the CI badge.
    """
    now = time.monotonic()
    if use_cache:
        cached = _ci_status_cache.get(repo_name)
        if cached is not None:
            cached_at, cached_value = cached
            if now - cached_at < CI_STATUS_CACHE_TTL_SECONDS:
                return cached_value

    url = f"https://api.github.com/repos/momonala/{repo_name}/actions/workflows/ci.yml/runs?per_page=1"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 404:
            logger.debug("No GitHub Actions CI workflow for %s", repo_name)
            _ci_status_cache[repo_name] = (now, None)
            return None
        response.raise_for_status()
        runs = response.json().get("workflow_runs")
    except requests.RequestException as exc:
        logger.error("Failed to fetch CI status for %s: %s", repo_name, exc)
        return "error"

    if not runs:
        logger.warning("No workflow runs found for %s", repo_name)
        return "error"

    conclusion = runs[0].get("conclusion")
    status = conclusion if conclusion in ("success", "failure") else "error"
    _ci_status_cache[repo_name] = (now, status)
    return status


def is_linux() -> bool:
    return platform.system() == "Linux"


def _list_systemd_units(unit_types: list[str]) -> list[str]:
    """Return loaded project units for the given systemd unit types."""
    cmd = ["systemctl", "list-units", "--no-legend", "--plain", "--all"]
    for unit_type in unit_types:
        cmd.extend(["--type", unit_type])
    cmd.append("projects_*")
    try:
        out = subprocess.check_output(cmd, text=True)
    except subprocess.CalledProcessError as exc:
        logger.warning("systemctl list-units failed: %s", exc)
        return []
    return [line.strip().split()[0] for line in out.strip().splitlines() if line.strip()]


def get_services(use_cache: bool = True) -> list[str]:
    global _services_cache
    now = time.monotonic()
    if use_cache and _services_cache is not None:
        cached_at, cached_services = _services_cache
        if now - cached_at < SERVICES_CACHE_TTL_SECONDS:
            return cached_services

    units = sorted(set(_list_systemd_units(["service", "timer"])))
    _services_cache = (now, units)
    return units


def _format_uptime(raw: str) -> str:
    """Convert systemd duration like '2 days 3h 15min 4s' to a single unit, e.g. '2d'."""
    weeks_match = re.search(r"(\d+)\s*week", raw)
    days_match = re.search(r"(\d+)\s*day", raw)
    hours_match = re.search(r"(\d+)\s*h", raw)
    minutes_match = re.search(r"(\d+)\s*min", raw)

    total_days = int(days_match.group(1)) if days_match else 0
    weeks = (int(weeks_match.group(1)) if weeks_match else 0) + total_days // 7
    days = total_days % 7

    if weeks:
        return f"{weeks}w"
    if days:
        return f"{days}d"
    if hours_match:
        return f"{hours_match.group(1)}h"
    if minutes_match:
        return f"{minutes_match.group(1)}m"
    return raw


def parse_uptime(status_text: str) -> str | None:
    match = re.search(r"Active: active \(running\) since .*?; (.*?) ago", status_text)
    return _format_uptime(match.group(1)) if match else None


def parse_timer_next(status_text: str) -> str | None:
    """Parse time until next trigger from timer status output."""
    match = re.search(r"Trigger:.*?;\s*(.+?)\s+left", status_text)
    return _format_uptime(match.group(1)) if match else None


def _parse_is_active(status_text: str) -> bool:
    """Return True for running services and waiting timers."""
    return bool(re.search(r"Active:\s+active\s+\(", status_text, re.IGNORECASE))


def _parse_status_field(status_text: str, field: str) -> str | None:
    """Return the value of a `Field: value` line in `systemctl status` output."""
    match = re.search(rf"{field}: (.*?)(?:\n|$)", status_text)
    return match.group(1).strip() if match else None


def parse_memory(status_text: str) -> str | None:
    return _parse_status_field(status_text, "Memory")


def parse_cpu(status_text: str) -> str | None:
    return _parse_status_field(status_text, "CPU")


def parse_last_error(status_text: str) -> str | None:
    return _parse_status_field(status_text, "Error")


def get_info_for_service(service: str, lines: int = 1000) -> str:
    result = subprocess.run(
        ["systemctl", "status", service, "--no-pager", f"--lines={lines}"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        # Timer-triggered backup services sit "inactive (dead)" between runs, which makes
        # `systemctl status` exit non-zero even though nothing is actually wrong.
        if "backup" in service.lower() and not result.stderr.strip():
            logger.debug("systemctl status non-zero for idle backup service %s", service)
        else:
            logger.warning("systemctl status failed for %s: %s", service, result.stderr.strip())
        return result.stdout + "\n" + result.stderr
    return result.stdout


def get_service_health(service: str) -> ServiceStatus:
    """Get minimal service status for first paint."""
    project_group, suffix = parse_service_name(service)
    result = subprocess.run(["systemctl", "is-active", service], text=True, capture_output=True)
    state = (result.stdout or "").strip().lower()
    is_active = state == "active"
    is_failed = state == "failed"
    return ServiceStatus(
        name=service,
        is_active=is_active,
        is_failed=is_failed,
        uptime=None,
        memory=None,
        cpu=None,
        last_error=None,
        full_status="",
        project_group=project_group,
        suffix=suffix,
        ci_status=None,
    )


def get_service_status(service: str, include_ci: bool = True, status_lines: int = 0) -> ServiceStatus:
    status_text = get_info_for_service(service, lines=status_lines)
    project_group, suffix = parse_service_name(service)
    is_active = _parse_is_active(status_text)
    is_failed = "active: failed" in status_text.lower()
    is_timer = service.endswith(".timer")

    # Only fetch CI status for primary project services (no suffix, not a timer)
    ci_status = get_ci_status(project_group) if include_ci and suffix is None and not is_timer else None

    uptime = None
    if is_active:
        uptime = parse_uptime(status_text)
    if is_timer and uptime is None:
        uptime = parse_timer_next(status_text)

    return ServiceStatus(
        name=service,
        is_active=is_active,
        is_failed=is_failed,
        uptime=uptime,
        memory=parse_memory(status_text),
        cpu=parse_cpu(status_text),
        last_error=parse_last_error(status_text),
        full_status=status_text,
        project_group=project_group,
        suffix=suffix,
        ci_status=ci_status,
    )


@dataclass
class SystemInfo:
    """Host (Raspberry Pi) vitals, read live from /proc and /sys. All fields optional;
    a field is None when the underlying source is unavailable (e.g. running off-Pi)."""

    hostname: str
    local_ip: str | None
    uptime: str | None
    boot_time: str | None
    temperature_c: float | None
    cpu_percent: float | None
    cpu_count: int | None
    memory_used_pct: float | None
    memory_used_mb: int | None
    memory_total_mb: int | None
    disk_used_pct: float | None
    disk_used_gb: float | None
    disk_total_gb: float | None


def _read_local_ip() -> str | None:
    """LAN IP of the primary network interface. Opens no connection — UDP sockets pick
    a route without sending packets, which is the standard way to ask the OS for this."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _read_cpu_temperature() -> float | None:
    """CPU temperature in Celsius from the kernel thermal zone (millidegrees)."""
    try:
        millidegrees = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return round(int(millidegrees) / 1000, 1)
    except (OSError, ValueError):
        return None


def _read_proc_stat_busy_total() -> tuple[int, int] | None:
    """Return (busy, total) jiffies from the aggregate 'cpu' line of /proc/stat."""
    try:
        first_line = Path("/proc/stat").read_text().splitlines()[0]
    except (OSError, IndexError):
        return None
    fields = [int(v) for v in first_line.split()[1:]]
    if len(fields) < 4:
        return None
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
    total = sum(fields)
    return total - idle, total


def cpu_percent_from_delta(first: tuple[int, int], second: tuple[int, int]) -> float | None:
    """CPU utilization percent from two (busy, total) /proc/stat snapshots."""
    busy_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, busy_delta / total_delta * 100)), 1)


def _read_cpu_percent() -> float | None:
    """Instantaneous CPU utilization, sampled over a short window from /proc/stat."""
    first = _read_proc_stat_busy_total()
    if first is None:
        return None
    time.sleep(0.1)
    second = _read_proc_stat_busy_total()
    if second is None:
        return None
    return cpu_percent_from_delta(first, second)


@dataclass(frozen=True)
class ServiceCounters:
    """Raw per-unit readings. Each is None when the cgroup or property is unavailable.

    memory_bytes is anonymous memory (heap + stack) from the cgroup's memory.stat, which excludes
    reclaimable file page cache. cpu_nsec (CPUUsageNSec) and written_bytes (io.stat wbytes, summed
    over devices) are cumulative since the unit started.
    """

    memory_bytes: int | None = None
    cpu_nsec: int | None = None
    written_bytes: int | None = None


def _systemctl_show(units: list[str], properties: list[str]) -> dict[str, dict[str, str]]:
    """Return {unit: {property: value}} from one `systemctl show` call.

    Raises:
        subprocess.CalledProcessError: systemctl exited non-zero.
    """
    if not units:
        return {}
    result = subprocess.run(
        ["systemctl", "show", f"--property=Id,{','.join(properties)}", *units],
        text=True,
        capture_output=True,
        check=True,
    )

    # `systemctl show <a> <b>` emits one blank-line-separated block per unit, but a unit type that
    # doesn't support a requested property (e.g. .timer units have no ControlGroup/CPUUsageNSec on
    # some systemd versions) can emit zero lines for that block, shifting every later block out of
    # argument order. Key each block by its own Id= line instead of trusting position.
    by_unit: dict[str, dict[str, str]] = {}
    for block in result.stdout.strip().split("\n\n"):
        raw_values: dict[str, str] = {}
        for line in block.splitlines():
            key, _, raw = line.partition("=")
            raw_values[key] = raw.strip()
        unit = raw_values.pop("Id", "")
        if unit:
            by_unit[unit] = raw_values
    return by_unit


def read_service_metrics(units: list[str]) -> dict[str, ServiceCounters]:
    """Return {unit: ServiceCounters} for the given units in one systemctl call."""
    try:
        shown = _systemctl_show(units, ["ControlGroup", "CPUUsageNSec"])
    except subprocess.CalledProcessError as exc:
        logger.warning("systemctl show failed: %s", exc.stderr.strip())
        return {}
    metrics: dict[str, ServiceCounters] = {}
    for unit, raw_values in shown.items():
        cgroup = raw_values.get("ControlGroup", "")
        metrics[unit] = ServiceCounters(
            memory_bytes=_read_cgroup_anon(cgroup) if cgroup else None,
            cpu_nsec=_parse_systemd_counter(raw_values.get("CPUUsageNSec", "")),
            written_bytes=read_cgroup_written_bytes(cgroup) if cgroup else None,
        )
    for unit in units:
        metrics.setdefault(unit, ServiceCounters())
    return metrics


def read_working_directories(units: list[str]) -> dict[str, Path]:
    """Return {unit: WorkingDirectory} for the units that set one (timers don't)."""
    return {
        unit: Path(raw_values["WorkingDirectory"])
        for unit, raw_values in _systemctl_show(units, ["WorkingDirectory"]).items()
        if raw_values.get("WorkingDirectory")
    }


def outermost_directories(directories: set[Path]) -> set[Path]:
    """Drop every directory nested inside another one in the set, so none is counted twice."""
    return {
        directory
        for directory in directories
        if not any(other != directory and directory.is_relative_to(other) for other in directories)
    }


def read_directory_size_kb(directory: Path) -> int | None:
    """Return a directory tree's on-disk size in KB via `du`, or None when it can't be measured."""
    try:
        result = subprocess.run(
            ["du", "-skx", str(directory)], text=True, capture_output=True, timeout=DU_TIMEOUT_SECONDS
        )
        return int(result.stdout.split()[0])
    except (subprocess.TimeoutExpired, OSError, IndexError, ValueError):
        logger.warning("Could not measure %s", directory)
        return None


def read_cgroup_written_bytes(cgroup_path: str) -> int | None:
    """Return bytes written to block devices by a cgroup, from io.stat (summed over devices).

    A parent slice keeps counting the writes of children that have since exited.
    """
    stat_path = Path("/sys/fs/cgroup") / cgroup_path.lstrip("/") / "io.stat"
    try:
        return sum_io_stat_written_bytes(stat_path.read_text())
    except OSError:
        return None


def sum_io_stat_written_bytes(io_stat: str) -> int:
    """Sum ``wbytes`` across the per-device lines of a cgroup io.stat file."""
    total = 0
    for line in io_stat.splitlines():
        for field in line.split()[1:]:
            key, _, value = field.partition("=")
            if key == "wbytes":
                total += int(value)
    return total


JOURNALD_CGROUP = "/system.slice/systemd-journald.service"
SYSTEM_SLICE_CGROUP = "/system.slice"
USER_SLICE_CGROUP = "/user.slice"
JOURNALCTL_TIMEOUT_SECONDS = 10


def read_journal_entries(
    after_cursor: str | None, since_ts: float
) -> tuple[dict[str, int], str | None] | None:
    """Count journal entries per systemd unit since the cursor (or since ``since_ts`` without one).

    Returns ({unit: entries}, cursor of the last entry read). Entries without a unit are keyed "".
    The cursor is None when nothing new was logged. Returns None when journalctl failed.
    """
    cmd = ["journalctl", "--quiet", "--no-pager", "-o", "json", "--output-fields=_SYSTEMD_UNIT"]
    cmd.append(f"--after-cursor={after_cursor}" if after_cursor else f"--since=@{int(since_ts)}")
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=JOURNALCTL_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("journalctl entry count failed")
        return None
    if result.returncode != 0:
        logger.warning("journalctl entry count failed: %s", result.stderr.strip())
        return None
    return parse_journal_entries(result.stdout)


def parse_journal_entries(json_lines: str) -> tuple[dict[str, int], str | None]:
    """Tally ``journalctl -o json`` lines by _SYSTEMD_UNIT, returning the counts and the last cursor."""
    counts: dict[str, int] = {}
    cursor = None
    for line in json_lines.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        unit = entry.get("_SYSTEMD_UNIT") or ""
        counts[unit] = counts.get(unit, 0) + 1
        cursor = entry.get("__CURSOR", cursor)
    return counts, cursor


def read_root_sectors_written() -> int | None:
    """Return the cumulative 512-byte sectors written to the root filesystem's block device since boot."""
    try:
        device = os.stat("/").st_dev
        fields = Path(f"/sys/dev/block/{os.major(device)}:{os.minor(device)}/stat").read_text().split()
        return int(fields[6])
    except (OSError, IndexError, ValueError):
        return None


def _read_cgroup_anon(cgroup_path: str) -> int | None:
    """Return anonymous memory bytes from a cgroup's memory.stat (excludes page cache)."""
    stat_path = Path("/sys/fs/cgroup") / cgroup_path.lstrip("/") / "memory.stat"
    try:
        text = stat_path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key == "anon":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _parse_systemd_counter(raw: str) -> int | None:
    """Parse a numeric systemd property; None when unset (empty or the SYSTEMD_UNSET sentinel)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return None if value == SYSTEMD_UNSET else value


def read_total_memory_bytes() -> int | None:
    """Return MemTotal in bytes, or None when /proc/meminfo is unreadable (non-Linux)."""
    try:
        meminfo = Path("/proc/meminfo").read_text()
    except OSError:
        return None
    for line in meminfo.splitlines():
        key, _, rest = line.partition(":")
        if key == "MemTotal":
            return int(rest.strip().split()[0]) * 1024  # value is in kB
    return None


def _read_memory() -> tuple[int, int, float] | None:
    """Return (used_mb, total_mb, used_pct) parsed from /proc/meminfo."""
    try:
        meminfo = Path("/proc/meminfo").read_text()
    except OSError:
        return None
    values = {}
    for line in meminfo.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.strip().split()[0])  # value is in kB
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    total_kb = values["MemTotal"]
    used_kb = total_kb - values["MemAvailable"]
    return round(used_kb / 1024), round(total_kb / 1024), round(used_kb / total_kb * 100, 1)


def get_system_info() -> SystemInfo:
    """Collect host vitals. Cheap enough to call per dashboard poll (~100ms for the CPU sample)."""
    memory = _read_memory()

    disk_used_pct = disk_used_gb = disk_total_gb = None
    try:
        usage = shutil.disk_usage("/")
        gb = 1024**3
        disk_total_gb = round(usage.total / gb, 1)
        disk_used_gb = round(usage.used / gb, 1)
        disk_used_pct = round(usage.used / usage.total * 100, 1)
    except OSError:
        pass

    return SystemInfo(
        hostname=platform.node(),
        local_ip=_read_local_ip(),
        uptime=_read_uptime(),
        boot_time=_read_boot_time(),
        temperature_c=_read_cpu_temperature(),
        cpu_percent=_read_cpu_percent(),
        cpu_count=os.cpu_count(),
        memory_used_mb=memory[0] if memory else None,
        memory_total_mb=memory[1] if memory else None,
        memory_used_pct=memory[2] if memory else None,
        disk_used_pct=disk_used_pct,
        disk_used_gb=disk_used_gb,
        disk_total_gb=disk_total_gb,
    )


def _read_uptime_seconds() -> float | None:
    """Seconds since boot from /proc/uptime, or None when it is unreadable (non-Linux)."""
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _read_uptime() -> str | None:
    """Host uptime as a compact human string (e.g. '3d')."""
    seconds = _read_uptime_seconds()
    if seconds is None:
        return None
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _read_boot_time() -> str | None:
    """Last reboot timestamp (local time, ISO 8601)."""
    seconds = _read_uptime_seconds()
    if seconds is None:
        return None
    return datetime.fromtimestamp(time.time() - seconds).isoformat()
