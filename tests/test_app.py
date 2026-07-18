"""Tests for app.py Flask application."""

import subprocess
from unittest.mock import patch

import pytest
from requests import RequestException

from src.app import app
from src.services import ServiceStatus
from src.values import INSPECTOR_DETECTOR_CWD, INSPECTOR_DETECTOR_UV_PATH


@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@patch("src.app.is_linux", return_value=True)
@patch("src.app.get_services")
@patch("src.app.get_service_health")
@patch("src.app.get_info_for_service")
def test_index(mock_get_info, mock_get_health, mock_get_services, mock_is_linux, client):
    """Index route renders with services and selected service info."""
    mock_get_services.return_value = ["projects_test1.service", "projects_test2.service"]
    mock_get_health.return_value = ServiceStatus(
        name="projects_test1.service",
        is_active=True,
        is_failed=False,
        uptime="1 day",
        memory="100M",
        cpu="50ms",
        last_error=None,
        full_status="",
        project_group="test1",
        suffix=None,
        ci_status="success",
    )
    mock_get_info.return_value = ""

    response = client.get("/")
    assert response.status_code == 200
    assert mock_get_health.call_count == 2

    mock_get_info.return_value = "Detailed service info"
    response = client.get("/?service=projects_test1.service")
    assert response.status_code == 200


@patch("src.app.is_linux", return_value=False)
def test_index_non_linux(mock_is_linux, client):
    """Index route uses canned data on non-Linux systems."""
    response = client.get("/")
    assert response.status_code == 200


@patch("src.app.is_linux", return_value=True)
@patch("src.app.get_services", return_value=["projects_test.service"])
@patch("src.app.subprocess.Popen")
def test_restart_service(mock_popen, mock_get_services, mock_is_linux, client):
    """Restart service triggers systemctl and redirects without waiting."""
    response = client.post("/restart", data={"service": "projects_test.service"}, follow_redirects=False)
    assert response.status_code == 302
    mock_popen.assert_called_once_with(
        ["sudo", "systemctl", "restart", "projects_test.service"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    mock_popen.side_effect = OSError("spawn failed")
    response = client.post("/restart", data={"service": "projects_test.service"})
    assert response.status_code == 500
    assert b"Failed to trigger restart for projects_test.service" in response.data

    response = client.post("/restart", data={})
    assert response.status_code == 400
    assert b"service parameter required" in response.data


@patch("src.app.subprocess.run")
def test_inspector_detector_check(mock_run, client):
    """Inspector Detector check runs command and redirects on success or returns error."""
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "Check completed"
    mock_run.return_value.stderr = ""

    response = client.post(
        "/inspector-detector/check", data={"service": "projects_train.service"}, follow_redirects=False
    )
    assert response.status_code == 302
    mock_run.assert_called_once_with(
        [INSPECTOR_DETECTOR_UV_PATH, "run", "-m", "scripts.check_inspections"],
        check=True,
        text=True,
        capture_output=True,
        cwd=INSPECTOR_DETECTOR_CWD,
    )

    mock_run.side_effect = subprocess.CalledProcessError(1, "uv", stderr="Script failed")
    response = client.post("/inspector-detector/check", data={"service": "projects_train.service"})
    assert response.status_code == 500
    assert b"Script failed" in response.data


@patch("src.app.is_linux", return_value=True)
@patch("src.app.get_services", return_value=["projects_test1.service"])
@patch("src.app.get_service_status")
def test_sidebar_details(mock_get_status, mock_get_services, mock_is_linux, client):
    """Sidebar details endpoint returns enriched service status JSON."""
    mock_get_status.return_value = ServiceStatus(
        name="projects_test1.service",
        is_active=True,
        is_failed=False,
        uptime="1 day",
        memory="100M",
        cpu="10s",
        last_error=None,
        full_status="",
        project_group="test1",
        suffix=None,
        ci_status="success",
    )

    response = client.get("/api/services/sidebar-details")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["services"][0]["name"] == "projects_test1.service"
    assert payload["services"][0]["ci_status"] == "success"
    assert "projects" not in payload


@patch("src.app.is_linux", return_value=True)
@patch("src.app.get_services", return_value=["projects_test1.service"])
def test_alert_settings_get(mock_get_services, mock_is_linux, client):
    """GET /api/alert-settings returns per-service frequencies, defaulting to hourly."""
    import src.scheduler as sched

    sched._alert_settings.clear()
    response = client.get("/api/alert-settings")
    assert response.status_code == 200
    data = response.get_json()
    assert data["projects_test1.service"] == "hourly"


@patch("src.app.is_linux", return_value=True)
@patch("src.app.get_services", return_value=["projects_test1.service"])
def test_alert_settings_post_mute_and_restore(mock_get_services, mock_is_linux, client):
    """POST /api/alert-settings saves frequency; muted setting persists in memory."""
    import src.scheduler as sched

    sched._alert_settings.clear()

    response = client.post(
        "/api/alert-settings",
        json={"service": "projects_test1.service", "frequency": "muted"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert sched._alert_settings.get("projects_test1.service") == "muted"

    # Confirm GET reflects the muted state
    response = client.get("/api/alert-settings")
    assert response.get_json()["projects_test1.service"] == "muted"

    # Restore to hourly
    client.post(
        "/api/alert-settings",
        json={"service": "projects_test1.service", "frequency": "hourly"},
    )
    assert sched._alert_settings.get("projects_test1.service") == "hourly"


def test_alert_settings_post_invalid(client):
    """POST /api/alert-settings rejects missing or invalid inputs."""
    response = client.post("/api/alert-settings", json={"service": "", "frequency": "muted"})
    assert response.status_code == 400

    response = client.post(
        "/api/alert-settings", json={"service": "projects_test1.service", "frequency": "never"}
    )
    assert response.status_code == 400

    response = client.post("/api/alert-settings", data="not json", content_type="text/plain")
    assert response.status_code == 400


@patch("src.app.send_telegram_message")
def test_send_alert_success(mock_send, client):
    """POST /api/alert sends the custom Markdown message to Telegram."""
    response = client.post("/api/alert", json={"message": "*Alert:* `disk full`"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    mock_send.assert_called_once_with("*Alert:* `disk full`")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": "   "},
        {"message": 123},
    ],
)
def test_send_alert_rejects_invalid_message(payload, client):
    """POST /api/alert requires a non-empty string message."""
    response = client.post("/api/alert", json=payload)
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


@patch("src.app.send_telegram_message", side_effect=RequestException("telegram down"))
def test_send_alert_telegram_failure(mock_send, client):
    """POST /api/alert returns 502 when Telegram delivery fails."""
    response = client.post("/api/alert", json={"message": "something broke"})
    assert response.status_code == 502
    assert response.get_json() == {"ok": False, "error": "failed to send alert"}
    mock_send.assert_called_once()
