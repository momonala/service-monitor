"""Tests for scheduler.py alert logic."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from requests import RequestException

import src.scheduler as sched
from src.scheduler import _should_alert, service_health_check, set_alert_frequency
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


def test_muted_service_never_alerts():
    set_alert_frequency("projects_foo.service", "muted")
    assert _should_alert("projects_foo.service") is False


def test_unknown_service_defaults_to_hourly_and_alerts_first_time():
    assert _should_alert("projects_unknown.service") is True


def test_hourly_suppresses_within_window():
    set_alert_frequency("projects_foo.service", "hourly")
    sched._alerted_services["projects_foo.service"] = datetime.now() - timedelta(minutes=30)
    assert _should_alert("projects_foo.service") is False


def test_hourly_alerts_after_window():
    set_alert_frequency("projects_foo.service", "hourly")
    sched._alerted_services["projects_foo.service"] = datetime.now() - timedelta(hours=2)
    assert _should_alert("projects_foo.service") is True


def test_daily_suppresses_within_same_window():
    set_alert_frequency("projects_foo.service", "daily")
    sched._alerted_services["projects_foo.service"] = datetime.now() - timedelta(hours=1)
    assert _should_alert("projects_foo.service") is False


def test_daily_alerts_after_reset():
    set_alert_frequency("projects_foo.service", "daily")
    # Last alert was before today's 6 AM reset window
    sched._alerted_services["projects_foo.service"] = datetime.now() - timedelta(days=1)
    assert _should_alert("projects_foo.service") is True


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
