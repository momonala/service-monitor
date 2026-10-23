"""Tests for host_info.py."""

import subprocess

import pytest

from src import host_info
from src.host_info import count_apt_upgradable, parse_throttled


@pytest.mark.parametrize(
    "output, now, since_boot",
    [
        ("throttled=0x0", [], []),
        ("throttled=0x50000", [], ["Under-voltage", "Throttled"]),
        ("throttled=0x50005", ["Under-voltage", "Throttled"], ["Under-voltage", "Throttled"]),
        ("throttled=0x80008", ["Soft temperature limit"], ["Soft temperature limit"]),
    ],
)
def test_parse_throttled(output, now, since_boot):
    assert parse_throttled(output) == (now, since_boot)


def test_parse_throttled_rejects_garbage():
    with pytest.raises(ValueError):
        parse_throttled("VCHI initialization failed")


def test_count_apt_upgradable_skips_header():
    output = (
        "Listing...\n"
        "libc6/stable-security 2.36-9+deb12u10 arm64 [upgradable from: 2.36-9+deb12u9]\n"
        "openssl/stable-security 3.0.17-1~deb12u1 arm64 [upgradable from: 3.0.16-1~deb12u1]\n"
    )
    assert count_apt_upgradable(output) == 2
    assert count_apt_upgradable("Listing...\n") == 0


def _fake_run(outputs: dict[str, tuple[int, str]]):
    def run(cmd, **_kwargs):
        returncode, stdout = outputs[cmd[0]]
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return run


def test_refresh_host_health_reads_every_check(monkeypatch, tmp_path):
    monkeypatch.setattr(
        host_info.subprocess,
        "run",
        _fake_run(
            {
                "vcgencmd": (0, "throttled=0x50000\n"),
                "systemctl": (3, "inactive\n"),
                "apt": (0, "Listing...\nlibc6/stable 2.36 arm64 [upgradable from: 2.35]\n"),
            }
        ),
    )
    reboot_flag = tmp_path / "reboot-required"
    reboot_flag.touch()
    monkeypatch.setattr(host_info, "REBOOT_REQUIRED_PATH", reboot_flag)
    monkeypatch.setattr(host_info, "TIMESYNC_STATE_DIR", tmp_path)

    host_info.check_apt_updates()
    host_info.refresh_host_health()

    assert host_info.get_host_health() == host_info.HostHealth(
        throttled_now=[],
        throttled_since_boot=["Under-voltage", "Throttled"],
        reboot_required=True,
        apt_updates=1,
        ntp_synced=False,
        tunnel_active=False,
    )


def test_refresh_host_health_marks_missing_tools_unavailable(monkeypatch, tmp_path):
    def missing(cmd, **_kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(host_info.subprocess, "run", missing)
    monkeypatch.setattr(host_info, "REBOOT_REQUIRED_PATH", tmp_path / "absent")
    monkeypatch.setattr(host_info, "TIMESYNC_STATE_DIR", tmp_path / "absent")

    host_info.check_apt_updates()
    host_info.refresh_host_health()

    health = host_info.get_host_health()
    assert health.throttled_now is None
    assert health.apt_updates is None
    assert health.ntp_synced is None
    assert health.tunnel_active is None
    assert health.reboot_required is False


def test_host_health_has_no_apt_count_until_checked(monkeypatch, tmp_path):
    monkeypatch.setattr(
        host_info.subprocess,
        "run",
        _fake_run(
            {
                "vcgencmd": (0, "throttled=0x0\n"),
                "systemctl": (0, "active\n"),
                "apt": (0, "Listing...\nlibc6/stable 2.36 arm64 [upgradable from: 2.35]\n"),
            }
        ),
    )
    monkeypatch.setattr(host_info, "REBOOT_REQUIRED_PATH", tmp_path / "absent")
    monkeypatch.setattr(host_info, "_apt_updates", None)

    host_info.refresh_host_health()
    assert host_info.get_host_health().apt_updates is None

    assert host_info.check_apt_updates() == 1
    assert host_info.get_host_health().apt_updates == 1


def test_ntp_synced_follows_timesyncd_flag_file(monkeypatch, tmp_path):
    monkeypatch.setattr(host_info, "TIMESYNC_STATE_DIR", tmp_path)
    assert host_info._read_ntp_synced() is False
    (tmp_path / "synchronized").touch()
    assert host_info._read_ntp_synced() is True
