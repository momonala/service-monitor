"""Tests for scheduler.py alert logic."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from requests import RequestException

import src.scheduler as sched
from src.scheduler import AlertState, on_check, service_health_check, set_alert_frequency
from src.services import ServiceStatus


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
        is_active=False,
        is_failed=True,
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
    assert on_check(failed=True, frequency="muted", last_alert=None, now=NOW) is AlertState.SUPPRESSED


def test_first_failure_fires():
    assert on_check(failed=True, frequency="hourly", last_alert=None, now=NOW) is AlertState.FIRING


def test_hourly_suppresses_within_window():
    last_alert = NOW - timedelta(minutes=30)
    assert on_check(failed=True, frequency="hourly", last_alert=last_alert, now=NOW) is AlertState.SUPPRESSED


def test_hourly_fires_after_window():
    last_alert = NOW - timedelta(hours=2)
    assert on_check(failed=True, frequency="hourly", last_alert=last_alert, now=NOW) is AlertState.FIRING


def test_daily_suppresses_within_same_window():
    last_alert = sched._alert_period_start(NOW) + timedelta(seconds=1)
    assert on_check(failed=True, frequency="daily", last_alert=last_alert, now=NOW) is AlertState.SUPPRESSED


def test_daily_fires_after_reset():
    last_alert = NOW - timedelta(days=1)
    assert on_check(failed=True, frequency="daily", last_alert=last_alert, now=NOW) is AlertState.FIRING


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
