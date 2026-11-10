"""Tests for scheduler.py alert logic."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from requests import RequestException

import src.scheduler as sched
from src.scheduler import AlertState, on_check, service_health_check
from src.services import ServiceState, ServiceStatus


@pytest.fixture(autouse=True)
def reset_scheduler_state():
    """Isolate each test: clear in-memory alert state."""
    sched._alert_settings.clear()
    sched._alerted_services.clear()
    yield
    sched._alert_settings.clear()
    sched._alerted_services.clear()


NOW = datetime(2026, 3, 10, 12, 0)


@pytest.mark.parametrize(
    "failed, frequency, last_alert, now, expected",
    [
        (False, "hourly", None, NOW, AlertState.HEALTHY),
        (True, "muted", None, NOW, AlertState.MUTED),
        (True, "hourly", None, NOW, AlertState.FIRING),
        (True, "hourly", NOW - timedelta(seconds=3599.9), NOW, AlertState.ALREADY_ALERTED),
        (True, "hourly", NOW - timedelta(seconds=3600), NOW, AlertState.FIRING),
        (True, "daily", datetime(2026, 3, 10, 6, 0, 0), NOW, AlertState.ALREADY_ALERTED),
        (True, "daily", datetime(2026, 3, 9, 12, 0), NOW, AlertState.FIRING),
        (
            True,
            "daily",
            datetime(2026, 3, 9, 6, 0, 0),
            datetime(2026, 3, 10, 3, 0),
            AlertState.ALREADY_ALERTED,
        ),
        (True, "daily", datetime(2026, 3, 9, 5, 59, 59), datetime(2026, 3, 10, 3, 0), AlertState.FIRING),
        (True, "daily", datetime(2026, 3, 10, 5, 59), datetime(2026, 3, 10, 6, 0), AlertState.FIRING),
    ],
    ids=[
        "healthy",
        "muted",
        "first_failure",
        "hourly_just_under_an_hour",
        "hourly_exactly_an_hour",
        "daily_at_window_start",
        "daily_previous_window",
        "daily_before_reset_hour_in_yesterdays_window",
        "daily_before_reset_hour_before_yesterdays_window",
        "daily_at_reset_hour_opens_new_window",
    ],
)
def test_on_check(failed, frequency, last_alert, now, expected):
    assert on_check(failed=failed, frequency=frequency, last_alert=last_alert, now=now) is expected


@patch("src.scheduler.datetime")
def test_unknown_service_uses_hourly_default(mock_datetime):
    mock_datetime.now.return_value = NOW
    sched._alerted_services["projects_unknown.service"] = NOW - timedelta(hours=2)
    assert sched._check_alert_state("projects_unknown.service", failed=True) is AlertState.FIRING


@patch("src.scheduler.send_service_failure_alert", side_effect=RequestException("down"))
@patch("src.scheduler.get_service_status")
@patch("src.scheduler.get_services", return_value=["projects_foo.service"])
def test_health_check_does_not_mark_alerted_when_send_fails(mock_get_services, mock_get_status, mock_alert):
    mock_get_status.return_value = ServiceStatus(
        name="projects_foo.service",
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
    service_health_check()
    mock_alert.assert_called_once()
    assert "projects_foo.service" not in sched._alerted_services


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "alert_settings.json"
    monkeypatch.setattr(sched, "_SETTINGS_FILE", path)
    return path


def test_load_settings_drops_invalid_frequencies(settings_file):
    settings_file.write_text('{"projects_a.service": "muted", "projects_b.service": "never"}')
    sched._load_settings()
    assert sched.get_all_alert_settings() == {"projects_a.service": "muted"}


def test_load_settings_ignores_corrupt_json(settings_file):
    settings_file.write_text("{not json")
    sched._load_settings()
    assert sched.get_all_alert_settings() == {}
    assert sched.get_alert_frequency("projects_a.service") == "hourly"


def test_saved_settings_load_back(settings_file):
    sched.set_alert_frequency("projects_a.service", "daily")
    sched._alert_settings.clear()
    sched._load_settings()
    assert sched.get_all_alert_settings() == {"projects_a.service": "daily"}
