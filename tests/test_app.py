"""Tests for app.py Flask application."""

import subprocess
from unittest.mock import patch

import pytest

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
