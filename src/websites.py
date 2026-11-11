"""Links to the project sites the Cloudflare Tunnel serves, built from the live tunnel config."""

import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yaml

from src.services import is_project_unit, parse_service_name

logger = logging.getLogger(__name__)

TUNNEL_CONFIG_PATH = Path("/etc/cloudflared/config.yml")
SS_TIMEOUT_SECONDS = 5
_SS_PID = re.compile(r"pid=(\d+)")


def read_tunnel_routes(config_path: Path = TUNNEL_CONFIG_PATH) -> list[tuple[str, int]]:
    """(hostname, local port) for every ingress rule that proxies HTTP to localhost, in config order."""
    try:
        config = yaml.safe_load(config_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Could not read tunnel config %s: %s", config_path, exc)
        return []
    routes = []
    for rule in config.get("ingress") or []:
        service = urlparse(rule.get("service", ""))
        if (
            rule.get("hostname")
            and service.scheme == "http"
            and service.hostname == "localhost"
            and service.port
        ):
            routes.append((rule["hostname"], service.port))
    return routes


def read_listening_units() -> dict[int, str]:
    """{port: systemd unit} for local TCP listeners whose process this user can see."""
    try:
        result = subprocess.run(
            ["ss", "-ltnpH"], text=True, capture_output=True, timeout=SS_TIMEOUT_SECONDS, check=True
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("ss failed: %s", exc)
        return {}
    units = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        pid = _SS_PID.search(line)
        if len(fields) < 4 or pid is None:
            continue
        unit = _unit_of_pid(int(pid.group(1)))
        if unit:
            units[int(fields[3].rsplit(":", 1)[1])] = unit
    return units


def _unit_of_pid(pid: int) -> str | None:
    try:
        cgroup = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    unit = cgroup.strip().rsplit("/", 1)[-1]
    return unit if unit.endswith(".service") else None


def project_websites(
    routes: list[tuple[str, int]], listening_units: dict[int, str], own_port: int
) -> list[dict[str, str]]:
    """One link per live project port, labelled by its hostname, sorted by label.

    Routes whose port no project listens on are dropped. When several hostnames reach the same port,
    the one named after the project wins, else the first in the config.
    """
    hostnames_by_port: dict[int, list[str]] = {}
    for hostname, port in routes:
        unit = listening_units.get(port)
        if port != own_port and unit is not None and is_project_unit(unit):
            hostnames_by_port.setdefault(port, []).append(hostname)
    websites = []
    for port, hostnames in hostnames_by_port.items():
        project = parse_service_name(listening_units[port])[0]
        hostname = next((h for h in hostnames if _subdomain(h) == project), hostnames[0])
        websites.append({"name": _subdomain(hostname), "project": project, "url": f"https://{hostname}"})
    return sorted(websites, key=lambda website: website["name"])


def _subdomain(hostname: str) -> str:
    return hostname.split(".", 1)[0]


def get_websites(own_port: int) -> list[dict[str, str]]:
    return project_websites(read_tunnel_routes(), read_listening_units(), own_port)
