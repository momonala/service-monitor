import functools
import logging
import platform
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT_SECONDS = 10
APT_TIMEOUT_SECONDS = 60
REBOOT_REQUIRED_PATH = Path("/var/run/reboot-required")
DEVICE_MODEL_PATH = Path("/proc/device-tree/model")
TUNNEL_UNIT = "cloudflared"

# Bit positions in `vcgencmd get_throttled`: low bits are the current state, high bits are sticky since boot.
THROTTLE_FLAGS = {
    0: "Under-voltage",
    1: "Frequency capped",
    2: "Throttled",
    3: "Soft temperature limit",
}
THROTTLE_SINCE_BOOT_OFFSET = 16


@dataclass(frozen=True)
class HostIdentity:
    """Facts about the machine that don't change while it is up. Fields are None when unreadable."""

    model: str | None
    os_name: str | None
    kernel: str
    arch: str
    python_version: str
    system_python_version: str | None


@dataclass(frozen=True)
class HostHealth:
    """Host warning flags. A field is None when its source is unavailable."""

    throttled_now: list[str] | None
    throttled_since_boot: list[str] | None
    reboot_required: bool
    apt_updates: int | None
    ntp_synced: bool | None
    tunnel_active: bool | None


def _run(cmd: list[str], timeout: float = SUBPROCESS_TIMEOUT_SECONDS) -> str | None:
    """Return a command's stripped stdout, or None when it fails or is missing."""
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("%s failed", cmd[0])
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _read_model() -> str | None:
    try:
        return DEVICE_MODEL_PATH.read_text().rstrip("\x00 \n") or None
    except OSError:
        return None


def _read_os_name() -> str | None:
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME")
    except OSError:
        return None


def _read_system_python_version() -> str | None:
    output = _run(["/usr/bin/python3", "--version"])
    return output.removeprefix("Python ").strip() if output else None


@functools.cache
def get_host_identity() -> HostIdentity:
    return HostIdentity(
        model=_read_model(),
        os_name=_read_os_name(),
        kernel=platform.release(),
        arch=platform.machine(),
        python_version=platform.python_version(),
        system_python_version=_read_system_python_version(),
    )


def parse_throttled(output: str) -> tuple[list[str], list[str]]:
    """Split `vcgencmd get_throttled` output (``throttled=0x50005``) into (active now, seen since boot)."""
    bits = int(output.partition("=")[2], 16)
    now = [label for bit, label in THROTTLE_FLAGS.items() if bits & (1 << bit)]
    since_boot = [
        label for bit, label in THROTTLE_FLAGS.items() if bits & (1 << (bit + THROTTLE_SINCE_BOOT_OFFSET))
    ]
    return now, since_boot


def _read_throttled() -> tuple[list[str], list[str]] | None:
    output = _run(["vcgencmd", "get_throttled"])
    if not output:
        return None
    try:
        return parse_throttled(output)
    except ValueError:
        logger.warning("Unexpected vcgencmd output: %s", output)
        return None


def count_apt_upgradable(output: str) -> int:
    """Count package lines in `apt list --upgradable` output, skipping the "Listing..." header."""
    return sum(1 for line in output.splitlines() if "/" in line)


def _read_apt_updates() -> int | None:
    output = _run(["apt", "list", "--upgradable"], timeout=APT_TIMEOUT_SECONDS)
    return None if output is None else count_apt_upgradable(output)


def _read_ntp_synced() -> bool | None:
    output = _run(["timedatectl", "show", "--property=NTPSynchronized", "--value"])
    return None if output is None else output == "yes"


def _read_tunnel_active() -> bool | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", TUNNEL_UNIT],
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout.strip() == "active"


_apt_updates: int | None = None
_health: HostHealth | None = None


def check_apt_updates() -> int | None:
    """Re-count pending apt upgrades. Takes seconds of CPU on a Pi, so it only runs when the user asks."""
    global _apt_updates
    _apt_updates = _read_apt_updates()
    return _apt_updates


def refresh_host_health() -> None:
    global _health
    throttled = _read_throttled()
    _health = HostHealth(
        throttled_now=throttled[0] if throttled else None,
        throttled_since_boot=throttled[1] if throttled else None,
        reboot_required=REBOOT_REQUIRED_PATH.exists(),
        apt_updates=None,
        ntp_synced=_read_ntp_synced(),
        tunnel_active=_read_tunnel_active(),
    )


def get_host_health() -> HostHealth | None:
    """Latest health snapshot with the last on-demand apt count, or None before the first refresh."""
    return replace(_health, apt_updates=_apt_updates) if _health else None
