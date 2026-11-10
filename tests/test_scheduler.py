"""Tests for scheduler.py alert logic."""

from datetime import datetime, timedelta
from enum import StrEnum
from unittest.mock import patch

import pytest
from requests import RequestException

import src.scheduler as sched
from src.scheduler import AlertState, on_check, service_health_check, set_alert_frequency
from src.services import ServiceState, ServiceStatus


@pytest.fixture(autouse=True)
def reset_scheduler_state():
    """Isolate each test: clear in-memory alert state."""
    sched._alert_settings.clear()
    sched._alerted_services.clear()
    yield
    sched._alert_settings.clear()
    sched._alerted_services.clear()


def _failed_service(name: str = "projects_foo.service") -> ServiceStatus:
    return ServiceStatus(
        name=name,
        state=ServiceState.FAILED,
        uptime=None,
        memory=None,
        cpu=None,
        last_error=None,
        full_status="",
        project_group="foo",
        suffix=None,
        ci_status=None,
    )


NOW = datetime(2026, 3, 10, 12, 0)


def test_healthy_service_never_alerts():
    assert on_check(failed=False, frequency="hourly", last_alert=None, now=NOW) is AlertState.HEALTHY


def test_muted_service_never_alerts():
    assert on_check(failed=True, frequency="muted", last_alert=None, now=NOW) is AlertState.MUTED


def test_first_failure_fires():
    assert on_check(failed=True, frequency="hourly", last_alert=None, now=NOW) is AlertState.FIRING


def test_hourly_suppresses_within_window():
    last_alert = NOW - timedelta(minutes=30)
    assert (
        on_check(failed=True, frequency="hourly", last_alert=last_alert, now=NOW)
        is AlertState.ALREADY_ALERTED
    )


def test_hourly_fires_at_exactly_one_hour():
    last_alert = NOW - timedelta(seconds=3600)
    assert on_check(failed=True, frequency="hourly", last_alert=last_alert, now=NOW) is AlertState.FIRING


def test_hourly_suppresses_just_under_one_hour():
    last_alert = NOW - timedelta(seconds=3599.9)
    assert (
        on_check(failed=True, frequency="hourly", last_alert=last_alert, now=NOW)
        is AlertState.ALREADY_ALERTED
    )


def test_hourly_fires_after_window():
    last_alert = NOW - timedelta(hours=2)
    assert on_check(failed=True, frequency="hourly", last_alert=last_alert, now=NOW) is AlertState.FIRING


def test_daily_suppresses_within_same_window():
    last_alert = datetime(2026, 3, 10, 6, 0, 1)
    assert (
        on_check(failed=True, frequency="daily", last_alert=last_alert, now=NOW) is AlertState.ALREADY_ALERTED
    )


def test_daily_suppresses_when_last_alert_equals_period_start():
    last_alert = datetime(2026, 3, 10, 6, 0, 0)
    assert (
        on_check(failed=True, frequency="daily", last_alert=last_alert, now=NOW) is AlertState.ALREADY_ALERTED
    )


def test_daily_before_reset_hour_uses_previous_day_window():
    now = datetime(2026, 3, 10, 3, 0)
    in_window = datetime(2026, 3, 9, 6, 0, 0)
    before_window = datetime(2026, 3, 9, 5, 59, 59)
    assert (
        on_check(failed=True, frequency="daily", last_alert=in_window, now=now) is AlertState.ALREADY_ALERTED
    )
    assert on_check(failed=True, frequency="daily", last_alert=before_window, now=now) is AlertState.FIRING


def test_daily_fires_after_reset():
    last_alert = NOW - timedelta(days=1)
    assert on_check(failed=True, frequency="daily", last_alert=last_alert, now=NOW) is AlertState.FIRING


def test_unknown_service_defaults_to_hourly_and_fires_first_time():
    assert sched._check_alert_state("projects_unknown.service", failed=True) is AlertState.FIRING


@patch("src.scheduler.datetime")
def test_unknown_service_uses_hourly_default(mock_datetime):
    mock_datetime.now.return_value = NOW
    sched._alerted_services["projects_unknown.service"] = NOW - timedelta(hours=2)
    assert sched._check_alert_state("projects_unknown.service", failed=True) is AlertState.FIRING


def test_set_invalid_frequency_raises():
    with pytest.raises(ValueError):
        set_alert_frequency("projects_foo.service", "never")


@patch("src.scheduler.send_service_failure_alert")
@patch("src.scheduler.get_service_status")
@patch("src.scheduler.get_services")
def test_health_check_skips_muted_service(mock_get_services, mock_get_status, mock_alert):
    mock_get_services.return_value = ["projects_foo.service"]
    mock_get_status.return_value = _failed_service()
    set_alert_frequency("projects_foo.service", "muted")
    service_health_check()
    mock_alert.assert_not_called()


@patch("src.scheduler.send_service_failure_alert")
@patch("src.scheduler.get_service_status")
@patch("src.scheduler.get_services")
def test_health_check_sends_alert_for_failed_service(mock_get_services, mock_get_status, mock_alert):
    mock_get_services.return_value = ["projects_foo.service"]
    mock_get_status.return_value = _failed_service()
    service_health_check()
    mock_alert.assert_called_once()
    assert "projects_foo.service" in sched._alerted_services


@patch("src.scheduler.send_service_failure_alert", side_effect=RequestException("down"))
@patch("src.scheduler.get_service_status")
@patch("src.scheduler.get_services")
def test_health_check_does_not_mark_alerted_when_send_fails(mock_get_services, mock_get_status, mock_alert):
    mock_get_services.return_value = ["projects_foo.service"]
    mock_get_status.return_value = _failed_service()
    service_health_check()
    mock_alert.assert_called_once()
    assert "projects_foo.service" not in sched._alerted_services


@patch("src.scheduler.send_service_failure_alert")
@patch("src.scheduler.get_service_status")
@patch("src.scheduler.get_services")
def test_health_check_suppresses_when_already_alerted(mock_get_services, mock_get_status, mock_alert, caplog):
    mock_get_services.return_value = ["projects_foo.service"]
    mock_get_status.return_value = _failed_service()
    service_health_check()
    caplog.clear()
    with caplog.at_level("INFO", logger="src.scheduler"):
        service_health_check()
    mock_alert.assert_called_once()
    messages = [record.getMessage() for record in caplog.records]
    assert "Service projects_foo.service has failed." in messages
    assert "Alert suppressed for projects_foo.service (muted or already sent)" in messages


class Check(StrEnum):
    FAILED = "failed"
    ACTIVE = "active"
    AUTO_RESTART = "auto_restart"
    INACTIVE = "inactive"
    STATUS_ERROR = "status_error"


def _status_for(check: Check) -> ServiceStatus:
    status = _failed_service()
    status.state = {
        Check.FAILED: ServiceState.FAILED,
        Check.ACTIVE: ServiceState.ACTIVE,
        Check.AUTO_RESTART: ServiceState.ACTIVATING,
        Check.INACTIVE: ServiceState.INACTIVE,
        Check.STATUS_ERROR: ServiceState.INACTIVE,
    }[check]
    status.full_status = {
        Check.FAILED: "Active: failed (Result: exit-code)",
        Check.ACTIVE: "Active: active (running)",
        Check.AUTO_RESTART: "Active: activating (auto-restart) (Result: exit-code)",
        Check.INACTIVE: "Active: inactive (dead)",
        Check.STATUS_ERROR: "\nFailed to get unit status",
    }[check]
    return status


def _run_checks(checks: list[tuple[datetime, Check]]) -> list[datetime]:
    sent_at: list[datetime] = []
    with (
        patch("src.scheduler.get_services", return_value=["projects_foo.service"]),
        patch("src.scheduler.get_service_status") as mock_get_status,
        patch("src.scheduler.send_service_failure_alert") as mock_alert,
        patch("src.scheduler.datetime") as mock_datetime,
    ):
        for now, check in checks:
            mock_datetime.now.return_value = now
            mock_get_status.return_value = _status_for(check)
            calls_before = mock_alert.call_count
            service_health_check()
            if mock_alert.call_count > calls_before:
                sent_at.append(now)
    return sent_at


def test_recovery_rearms_hourly_alert():
    set_alert_frequency("projects_foo.service", "hourly")
    fail, recover, fail_again = NOW, NOW + timedelta(minutes=5), NOW + timedelta(minutes=10)
    checks = [(fail, Check.FAILED), (recover, Check.ACTIVE), (fail_again, Check.FAILED)]
    assert _run_checks(checks) == [fail, fail_again]


def test_recovery_rearms_daily_alert():
    set_alert_frequency("projects_foo.service", "daily")
    fail, recover, fail_again = NOW, NOW + timedelta(hours=2), NOW + timedelta(hours=4)
    checks = [(fail, Check.FAILED), (recover, Check.ACTIVE), (fail_again, Check.FAILED)]
    assert _run_checks(checks) == [fail, fail_again]


@pytest.mark.parametrize("between", [Check.AUTO_RESTART, Check.INACTIVE, Check.STATUS_ERROR])
def test_not_running_between_failures_stays_suppressed(between):
    set_alert_frequency("projects_foo.service", "hourly")
    checks = [
        (NOW, Check.FAILED),
        (NOW + timedelta(minutes=5), between),
        (NOW + timedelta(minutes=10), Check.FAILED),
    ]
    assert _run_checks(checks) == [NOW]


def test_continuous_failure_stays_suppressed():
    set_alert_frequency("projects_foo.service", "hourly")
    checks = [(NOW + timedelta(minutes=minutes), Check.FAILED) for minutes in (0, 5, 10, 55)]
    assert _run_checks(checks) == [NOW]


def test_muted_service_stays_muted_across_recovery():
    set_alert_frequency("projects_foo.service", "muted")
    checks = [
        (NOW, Check.FAILED),
        (NOW + timedelta(minutes=5), Check.ACTIVE),
        (NOW + timedelta(minutes=10), Check.FAILED),
    ]
    assert _run_checks(checks) == []


def test_recovery_without_prior_alert_is_noop():
    first_failure = NOW + timedelta(minutes=5)
    assert _run_checks([(NOW, Check.ACTIVE), (first_failure, Check.FAILED)]) == [first_failure]
