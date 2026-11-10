"""Characterization of service state derivation and the alert decisions built on it."""

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import src.scheduler as sched
from src.scheduler import service_health_check, set_alert_frequency
from src.services import get_service_health, get_service_status
from src.telegram import _format_service_failure_alert

SERVICE = "projects_foo.service"
TIMER = "projects_foo_ping.timer"
NOW = datetime(2026, 11, 10, 12, 0)


@dataclass(frozen=True)
class Fixture:
    status_stdout: str
    status_stderr: str
    status_returncode: int
    is_active_stdout: str
    is_active_returncode: int
    expected_active: bool
    expected_failed: bool
    unit: str = SERVICE


FIXTURES = {
    "active": Fixture(
        status_stdout=(
            "● projects_foo.service - Foo dashboard\n"
            "     Loaded: loaded (/etc/systemd/system/projects_foo.service; enabled; preset: enabled)\n"
            "     Active: active (running) since Tue 2026-11-10 09:55:12 CET; 2h 4min ago\n"
            "   Main PID: 1234 (python)\n"
            "      Tasks: 3 (limit: 4915)\n"
            "        CPU: 1min 2.345s\n"
            "     Memory: 45.2M (peak: 51.0M)\n"
            "     CGroup: /system.slice/projects_foo.service\n"
            "             └─1234 /home/mnalavadi/foo/.venv/bin/python -m src.app\n"
        ),
        status_stderr="",
        status_returncode=0,
        is_active_stdout="active\n",
        is_active_returncode=0,
        expected_active=True,
        expected_failed=False,
    ),
    "timer_waiting": Fixture(
        status_stdout=(
            "● projects_foo_ping.timer - Ping foo\n"
            "     Loaded: loaded (/etc/systemd/system/projects_foo_ping.timer; enabled; preset: enabled)\n"
            "     Active: active (waiting) since Mon 2026-11-09 08:00:00 CET; 1 day 4h ago\n"
            "    Trigger: Tue 2026-11-10 12:15:00 CET; 15min left\n"
            "   Triggers: ● projects_foo_ping.service\n"
        ),
        status_stderr="",
        status_returncode=0,
        is_active_stdout="active\n",
        is_active_returncode=0,
        expected_active=True,
        expected_failed=False,
        unit=TIMER,
    ),
    "failed": Fixture(
        status_stdout=(
            "× projects_foo.service - Foo dashboard\n"
            "     Loaded: loaded (/etc/systemd/system/projects_foo.service; enabled; preset: enabled)\n"
            "     Active: failed (Result: exit-code) since Tue 2026-11-10 11:57:01 CET; 2min 59s ago\n"
            "   Duration: 2.104s\n"
            "    Process: 1234 ExecStart=/home/mnalavadi/foo/.venv/bin/python -m src.app "
            "(code=exited, status=1/FAILURE)\n"
            "   Main PID: 1234 (code=exited, status=1/FAILURE)\n"
            "        CPU: 1.201s\n"
        ),
        status_stderr="",
        status_returncode=3,
        is_active_stdout="failed\n",
        is_active_returncode=3,
        expected_active=False,
        expected_failed=True,
    ),
    "inactive": Fixture(
        status_stdout=(
            "○ projects_foo.service - Foo dashboard\n"
            "     Loaded: loaded (/etc/systemd/system/projects_foo.service; disabled; preset: enabled)\n"
            "     Active: inactive (dead) since Tue 2026-11-10 10:00:03 CET; 1h 59min ago\n"
            "TriggeredBy: ● projects_foo_ping.timer\n"
            "   Main PID: 99 (code=exited, status=0/SUCCESS)\n"
            "        CPU: 300ms\n"
        ),
        status_stderr="",
        status_returncode=3,
        is_active_stdout="inactive\n",
        is_active_returncode=3,
        expected_active=False,
        expected_failed=False,
    ),
    "auto_restart": Fixture(
        status_stdout=(
            "● projects_foo.service - Foo dashboard\n"
            "     Loaded: loaded (/etc/systemd/system/projects_foo.service; enabled; preset: enabled)\n"
            "     Active: activating (auto-restart) (Result: exit-code) since Tue 2026-11-10 11:59:58 CET; "
            "2s ago\n"
            "    Process: 1240 ExecStart=/home/mnalavadi/foo/.venv/bin/python -m src.app "
            "(code=exited, status=1/FAILURE)\n"
            "   Main PID: 1240 (code=exited, status=1/FAILURE)\n"
            "        CPU: 980ms\n"
        ),
        status_stderr="",
        status_returncode=3,
        is_active_stdout="activating\n",
        is_active_returncode=3,
        expected_active=False,
        expected_failed=False,
    ),
    "unit_not_found": Fixture(
        status_stdout="",
        status_stderr="Unit projects_foo.service could not be found.\n",
        status_returncode=4,
        is_active_stdout="inactive\n",
        is_active_returncode=4,
        expected_active=False,
        expected_failed=False,
    ),
    "unit_not_found_old_systemd": Fixture(
        status_stdout="",
        status_stderr="Unit projects_foo.service could not be found.\n",
        status_returncode=4,
        is_active_stdout="unknown\n",
        is_active_returncode=4,
        expected_active=False,
        expected_failed=False,
    ),
    "command_error": Fixture(
        status_stdout="",
        status_stderr="Failed to connect to bus: No such file or directory\n",
        status_returncode=1,
        is_active_stdout="",
        is_active_returncode=1,
        expected_active=False,
        expected_failed=False,
    ),
}


def _fake_systemctl(fixture: Fixture):
    def run(cmd, **_kwargs):
        if cmd[1] == "is-active":
            return subprocess.CompletedProcess(
                cmd, fixture.is_active_returncode, fixture.is_active_stdout, ""
            )
        if cmd[1] == "status":
            return subprocess.CompletedProcess(
                cmd, fixture.status_returncode, fixture.status_stdout, fixture.status_stderr
            )
        raise AssertionError(f"unexpected command {cmd}")

    return run


@pytest.fixture(autouse=True)
def reset_scheduler_state():
    sched._alert_settings.clear()
    sched._alerted_services.clear()
    yield
    sched._alert_settings.clear()
    sched._alerted_services.clear()


@pytest.mark.parametrize("name", FIXTURES)
def test_full_status_flags(name):
    fixture = FIXTURES[name]
    with (
        patch("src.services.subprocess.run", side_effect=_fake_systemctl(fixture)),
        patch("src.services.get_ci_status", return_value=None),
    ):
        status = get_service_status(fixture.unit)
    assert (status.is_active, status.is_failed) == (fixture.expected_active, fixture.expected_failed)


@pytest.mark.parametrize("name", FIXTURES)
def test_minimal_health_flags(name):
    fixture = FIXTURES[name]
    with patch("src.services.subprocess.run", side_effect=_fake_systemctl(fixture)):
        status = get_service_health(fixture.unit)
    assert (status.is_active, status.is_failed) == (fixture.expected_active, fixture.expected_failed)


@pytest.mark.parametrize("name", FIXTURES)
def test_minimal_and_full_paths_agree_on_state(name):
    fixture = FIXTURES[name]
    with (
        patch("src.services.subprocess.run", side_effect=_fake_systemctl(fixture)),
        patch("src.services.get_ci_status", return_value=None),
    ):
        assert get_service_health(fixture.unit).state is get_service_status(fixture.unit).state


@pytest.mark.parametrize("name", FIXTURES)
def test_full_status_fields_derived_from_state(name):
    fixture = FIXTURES[name]
    with (
        patch("src.services.subprocess.run", side_effect=_fake_systemctl(fixture)),
        patch("src.services.get_ci_status", return_value="success"),
    ):
        status = get_service_status(fixture.unit)
    expected_uptime = {"active": "2h", "timer_waiting": "15m"}.get(name)
    assert status.uptime == expected_uptime
    assert status.ci_status == (None if fixture.unit == TIMER else "success")
    assert status.last_error is None


def _alerts_sent(checks: list[tuple[datetime, str]]) -> list[datetime]:
    sent_at: list[datetime] = []
    with (
        patch("src.scheduler.get_services", return_value=[SERVICE]),
        patch("src.services.get_ci_status", return_value=None),
        patch("src.scheduler.send_service_failure_alert") as mock_alert,
        patch("src.scheduler.datetime") as mock_datetime,
        patch("src.services.subprocess.run") as mock_run,
    ):
        for now, name in checks:
            mock_datetime.now.return_value = now
            mock_run.side_effect = _fake_systemctl(FIXTURES[name])
            calls_before = mock_alert.call_count
            service_health_check()
            if mock_alert.call_count > calls_before:
                sent_at.append(now)
    return sent_at


@pytest.mark.parametrize("name", FIXTURES)
def test_single_check_alerts_only_when_failed(name):
    expected = [NOW] if FIXTURES[name].expected_failed else []
    assert _alerts_sent([(NOW, name)]) == expected


@pytest.mark.parametrize("between", [name for name in FIXTURES if name != "failed"])
def test_only_running_rearms_alert_between_failures(between):
    set_alert_frequency(SERVICE, "hourly")
    later, last = NOW + timedelta(minutes=5), NOW + timedelta(minutes=10)
    sent = _alerts_sent([(NOW, "failed"), (later, between), (last, "failed")])
    expected = [NOW, last] if FIXTURES[between].expected_active else [NOW]
    assert sent == expected


def test_failure_alert_reports_derived_flags():
    with (
        patch("src.services.subprocess.run", side_effect=_fake_systemctl(FIXTURES["failed"])),
        patch("src.services.get_ci_status", return_value=None),
    ):
        status = get_service_status(SERVICE)
    message = _format_service_failure_alert(status)
    assert "*Is Active:* `False`\n*Is Failed:* `True`\n" in message


def test_failed_text_anywhere_outranks_active_so_the_alert_still_fires():
    status_text = (
        "● projects_foo.service - Foo dashboard\n"
        "     Active: active (running) since Tue 2026-11-10 09:55:12 CET; 2h 4min ago\n"
        '     Status: "upstream reported Active: failed"\n'
    )
    with patch("src.services.get_info_for_service", return_value=status_text):
        status = get_service_status(SERVICE, include_ci=False)
    assert status.is_failed
    assert _alerts_sent_for_text(status_text) == [NOW]


def _alerts_sent_for_text(status_text: str) -> list[datetime]:
    with (
        patch("src.scheduler.get_services", return_value=[SERVICE]),
        patch("src.services.get_ci_status", return_value=None),
        patch("src.services.get_info_for_service", return_value=status_text),
        patch("src.scheduler.send_service_failure_alert") as mock_alert,
        patch("src.scheduler.datetime") as mock_datetime,
    ):
        mock_datetime.now.return_value = NOW
        service_health_check()
    return [NOW] * mock_alert.call_count
