import logging
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from src.values import GITHUB_TOKEN

logger = logging.getLogger(__name__)

SERVICES_CACHE_TTL_SECONDS = 5.0
CI_STATUS_CACHE_TTL_SECONDS = 60.0
_services_cache: tuple[float, list[str]] | None = None
_ci_status_cache: dict[str, tuple[float, str]] = {}


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


def get_ci_status(repo_name: str, use_cache: bool = True) -> str:
    """Get CI status from GitHub Actions API with a short TTL cache."""
    now = time.monotonic()
    if use_cache:
        cached = _ci_status_cache.get(repo_name)
        if cached is not None:
            cached_at, cached_value = cached
            if now - cached_at < CI_STATUS_CACHE_TTL_SECONDS:
                return cached_value

    url = f"https://api.github.com/repos/momonala/{repo_name}/actions/workflows/ci.yml/runs?per_page=1"
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        if not data.get("workflow_runs"):
            logger.warning("No workflow runs found for %s", repo_name)
            return "error"
        latest_run = data["workflow_runs"][0]
        conclusion = latest_run.get("conclusion")
        if conclusion == "success":
            status = "success"
        elif conclusion == "failure":
            status = "failure"
        else:
            status = "error"
        _ci_status_cache[repo_name] = (now, status)
        return status
    except requests.RequestException as exc:
        logger.error("Failed to fetch CI status for %s: %s", repo_name, exc)
        return "error"
    except KeyError as exc:
        logger.error("Unexpected API response format for %s: %s", repo_name, exc)
        return "error"


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
    """Convert systemd duration like '2 days 3h 15min 4s' to '2d 3h', capped at 2 parts."""
    weeks_match = re.search(r"(\d+)\s*week", raw)
    days_match = re.search(r"(\d+)\s*day", raw)
    hours_match = re.search(r"(\d+)\s*h", raw)
    minutes_match = re.search(r"(\d+)\s*min", raw)

    total_days = int(days_match.group(1)) if days_match else 0
    explicit_weeks = int(weeks_match.group(1)) if weeks_match else 0
    w = explicit_weeks + total_days // 7
    d = total_days % 7

    parts = []
    if w:
        parts.append(f"{w}w")
    if d:
        parts.append(f"{d}d")
    if hours_match:
        parts.append(f"{hours_match.group(1)}h")
    if minutes_match:
        parts.append(f"{minutes_match.group(1)}m")
    return " ".join(parts[:2]) if parts else raw


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


def parse_memory(status_text: str) -> str | None:
    match = re.search(r"Memory: (.*?)(?:\n|$)", status_text)
    return match.group(1).strip() if match else None


def parse_cpu(status_text: str) -> str | None:
    match = re.search(r"CPU: (.*?)(?:\n|$)", status_text)
    return match.group(1).strip() if match else None


def parse_last_error(status_text: str) -> str | None:
    match = re.search(r"Error: (.*?)(?:\n|$)", status_text)
    return match.group(1).strip() if match else None


def get_info_for_service(service: str, lines: int = 1000) -> str:
    result = subprocess.run(
        ["systemctl", "status", service, "--no-pager", f"--lines={lines}"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
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
    ci_status = None
    if include_ci and suffix is None and not is_timer:
        ci_status = get_ci_status(project_group)

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
    uptime: str | None
    temperature_c: float | None
    cpu_percent: float | None
    load_avg: float | None  # 1-minute load average
    cpu_count: int | None
    memory_used_pct: float | None
    memory_used_mb: int | None
    memory_total_mb: int | None
    disk_used_pct: float | None
    disk_used_gb: float | None
    disk_total_gb: float | None


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


def _read_cpu_percent() -> float | None:
    """Instantaneous CPU utilization, sampled over a short window from /proc/stat."""
    first = _read_proc_stat_busy_total()
    if first is None:
        return None
    time.sleep(0.1)
    second = _read_proc_stat_busy_total()
    if second is None:
        return None
    busy_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, busy_delta / total_delta * 100)), 1)


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
    try:
        load_avg: float | None = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        load_avg = None

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
        uptime=_read_uptime(),
        temperature_c=_read_cpu_temperature(),
        cpu_percent=_read_cpu_percent(),
        load_avg=load_avg,
        cpu_count=os.cpu_count(),
        memory_used_mb=memory[0] if memory else None,
        memory_total_mb=memory[1] if memory else None,
        memory_used_pct=memory[2] if memory else None,
        disk_used_pct=disk_used_pct,
        disk_used_gb=disk_used_gb,
        disk_total_gb=disk_total_gb,
    )


def _read_uptime() -> str | None:
    """Host uptime as a compact human string (e.g. '3d 4h'), from /proc/uptime."""
    try:
        seconds = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts[:2]) if parts else "0m"
