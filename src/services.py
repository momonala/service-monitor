import logging
import platform
import re
import subprocess
import time
from dataclasses import dataclass

import requests

try:
    from src.values import GITHUB_TOKEN
except ImportError:
    GITHUB_TOKEN = None

logging.basicConfig(level=logging.INFO)
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
    """Parse service name to extract project group and optional suffix.

    Returns:
        tuple[str, str | None]: (project_group, suffix) where suffix is None if no suffix exists.

    Examples:
        'projects_energy-monitor.service' -> ('energy-monitor', None)
        'projects_energy-monitor_data-backup-scheduler.service' -> ('energy-monitor', 'data-backup-scheduler')
    """
    service_name = service_name.replace(".service", "")
    service_name = service_name.replace("projects_", "")
    parts = service_name.split("_")
    project_group = parts[0]
    suffix = "_".join(parts[1:]) if len(parts) > 1 else None
    return (project_group, suffix)


def get_github_repo_name(project_group: str) -> str:
    """Map project_group to GitHub repo name."""
    return project_group


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


def is_linux():
    return platform.system() == "Linux"


def get_services(use_cache: bool = True) -> list[str]:
    global _services_cache
    now = time.monotonic()
    if use_cache and _services_cache is not None:
        cached_at, cached_services = _services_cache
        if now - cached_at < SERVICES_CACHE_TTL_SECONDS:
            return cached_services

    out = subprocess.check_output(
        ["systemctl", "list-units", "--type=service", "--no-legend", "--plain", "projects_*"],
        text=True,
    )
    services = [line.strip().split()[0] for line in out.strip().splitlines()]
    _services_cache = (now, services)
    return services


def _format_uptime(raw: str) -> str:
    """Convert systemd duration like '2 days 3h 15min 4s' to '2d 3h', capped at 2 parts."""
    weeks = re.search(r"(\d+)\s*week", raw)
    days = re.search(r"(\d+)\s*day", raw)
    hours = re.search(r"(\d+)\s*h", raw)
    minutes = re.search(r"(\d+)\s*min", raw)

    total_days = int(days.group(1)) if days else 0
    w = total_days // 7
    d = total_days % 7

    parts = []
    if weeks:
        w += int(weeks.group(1))
    if w:
        parts.append(f"{w}w")
    if d:
        parts.append(f"{d}d")
    if hours:
        parts.append(f"{hours.group(1)}h")
    if minutes:
        parts.append(f"{minutes.group(1)}m")
    return " ".join(parts[:2]) if parts else raw


def parse_uptime(status_text):
    match = re.search(r"Active: active \(running\) since .*?; (.*?) ago", status_text)
    return _format_uptime(match.group(1)) if match else None


def parse_memory(status_text):
    match = re.search(r"Memory: (.*?)(?:\n|$)", status_text)
    return match.group(1).strip() if match else None


def parse_cpu(status_text):
    match = re.search(r"CPU: (.*?)(?:\n|$)", status_text)
    return match.group(1).strip() if match else None


def parse_last_error(status_text):
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
    is_active = "active (running)" in status_text.lower()

    # Only fetch CI status for services without suffixes
    ci_status = None
    if include_ci and suffix is None:
        repo_name = get_github_repo_name(project_group)
        ci_status = get_ci_status(repo_name)

    return ServiceStatus(
        name=service,
        is_active=is_active,
        is_failed="failed (result: exit-code)" in status_text.lower(),
        uptime=parse_uptime(status_text) if is_active else None,
        memory=parse_memory(status_text),
        cpu=parse_cpu(status_text),
        last_error=parse_last_error(status_text),
        full_status=status_text,
        project_group=project_group,
        suffix=suffix,
        ci_status=ci_status,
    )
