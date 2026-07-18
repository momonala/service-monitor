"""Tests for services.py module."""

from unittest.mock import patch

import pytest

import src.services as services_module
from src.canned_info import canned_service_statuses
from src.services import (
    SystemInfo,
    aggregate_project_resources,
    format_cpu_seconds,
    format_memory_bytes,
    get_ci_status,
    get_info_for_service,
    get_service_status,
    get_services,
    get_system_info,
    parse_cpu,
    parse_cpu_seconds,
    parse_last_error,
    parse_memory,
    parse_memory_bytes,
    parse_service_name,
    parse_timer_next,
    parse_uptime,
)


@pytest.fixture(autouse=True)
def reset_service_caches():
    services_module._services_cache = None
    services_module._ci_status_cache.clear()
    yield


@pytest.mark.parametrize(
    "status_text,expected",
    [
        ("Active: active (running) since Mon; 4 days ago", "4d"),
        ("Active: active (running) since Mon; 2h 15min ago", "2h 15m"),
        ("Active: inactive (dead) since Mon; 4 days ago", None),
        ("", None),
    ],
)
def test_parse_uptime(status_text, expected):
    """Parse uptime extracts duration from active services only."""
    assert parse_uptime(status_text) == expected


@pytest.mark.parametrize(
    "status_text,expected",
    [
        ("Memory: 123.4M\n", "123.4M"),
        ("Memory: 1.2G\nCPU: 100ms", "1.2G"),
        ("No memory info", None),
    ],
)
def test_parse_memory(status_text, expected):
    """Parse memory extracts memory usage or None."""
    assert parse_memory(status_text) == expected


@pytest.mark.parametrize(
    "status_text,expected",
    [
        ("CPU: 1h 23min 45.678s\nMemory: 100M", "1h 23min 45.678s"),
        ("No cpu info", None),
    ],
)
def test_parse_cpu(status_text, expected):
    """Parse CPU extracts CPU time or None."""
    assert parse_cpu(status_text) == expected


@pytest.mark.parametrize(
    "memory,expected_bytes",
    [
        ("123.4M", round(123.4 * 1024**2)),
        ("1.2G", round(1.2 * 1024**3)),
        ("512K", 512 * 1024),
        (None, None),
        ("", None),
    ],
)
def test_parse_memory_bytes(memory, expected_bytes):
    """Parse memory strings into bytes."""
    assert parse_memory_bytes(memory) == expected_bytes


@pytest.mark.parametrize(
    "total_bytes,expected",
    [
        (512, "512B"),
        (1536, "1.5K"),
        (129499136, "123.5M"),
    ],
)
def test_format_memory_bytes(total_bytes, expected):
    """Format bytes as compact memory strings."""
    assert format_memory_bytes(total_bytes) == expected


@pytest.mark.parametrize(
    "cpu,expected_seconds",
    [
        ("270ms", 0.27),
        ("2.417s", 2.417),
        ("7min 52.884s", 7 * 60 + 52.884),
        ("1h 23min 45.678s", 3600 + 23 * 60 + 45.678),
        (None, None),
    ],
)
def test_parse_cpu_seconds(cpu, expected_seconds):
    """Parse CPU time strings into seconds."""
    result = parse_cpu_seconds(cpu)
    if expected_seconds is None:
        assert result is None
    else:
        assert result == pytest.approx(expected_seconds)


@pytest.mark.parametrize(
    "total_seconds,expected",
    [
        (0.27, "270ms"),
        (45.0, "45s"),
        (125.0, "2m 5s"),
        (5025.0, "1h 23m"),
    ],
)
def test_format_cpu_seconds(total_seconds, expected):
    """Format CPU seconds as compact strings."""
    assert format_cpu_seconds(total_seconds) == expected


def test_aggregate_project_resources():
    """Aggregate memory and CPU across services in the same project group."""
    statuses = [
        services_module.ServiceStatus(
            name="projects_energy-monitor.service",
            is_active=True,
            is_failed=False,
            uptime=None,
            memory="100M",
            cpu="5min",
            last_error=None,
            full_status="",
            project_group="energy-monitor",
            suffix=None,
            ci_status=None,
        ),
        services_module.ServiceStatus(
            name="projects_energy-monitor_mqtt.service",
            is_active=True,
            is_failed=False,
            uptime=None,
            memory="23.4M",
            cpu="2min 30s",
            last_error=None,
            full_status="",
            project_group="energy-monitor",
            suffix="mqtt",
            ci_status=None,
        ),
        services_module.ServiceStatus(
            name="projects_pingpong.service",
            is_active=True,
            is_failed=False,
            uptime=None,
            memory="50M",
            cpu="1h",
            last_error=None,
            full_status="",
            project_group="pingpong",
            suffix=None,
            ci_status=None,
        ),
    ]
    resources = aggregate_project_resources(statuses)
    assert resources["energy-monitor"].memory == format_memory_bytes(
        parse_memory_bytes("100M") + parse_memory_bytes("23.4M")
    )
    assert resources["energy-monitor"].cpu == format_cpu_seconds(5 * 60 + 2 * 60 + 30)
    assert resources["pingpong"].memory == "50.0M"
    assert resources["pingpong"].cpu == "1h"


@pytest.mark.parametrize(
    "status_text,expected",
    [
        ("Error: Service failed\n", "Service failed"),
        ("Error: Command returned exit code 1\nOther info", "Command returned exit code 1"),
        ("No errors here", None),
    ],
)
def test_parse_last_error(status_text, expected):
    """Parse last error extracts error messages or None."""
    assert parse_last_error(status_text) == expected


@pytest.mark.parametrize(
    "service",
    canned_service_statuses,
)
def test_parse_service_name(service):
    """Parse service name extracts project group and suffix correctly."""
    project_group, suffix = parse_service_name(service.name)
    assert project_group == service.project_group
    assert suffix == service.suffix


@patch("src.services.subprocess.check_output")
def test_get_services(mock_check_output):
    """Parse systemctl list-units output for services and timers."""
    mock_check_output.return_value = (
        "projects_test1.service       loaded active running   Test Service 1\n"
        "projects_test2.service       loaded active running   Test Service 2\n"
        "projects_test_ping.timer     loaded active waiting   Daily ping timer\n"
    )
    assert get_services(use_cache=False) == [
        "projects_test1.service",
        "projects_test2.service",
        "projects_test_ping.timer",
    ]

    mock_check_output.return_value = ""
    assert get_services(use_cache=False) == []


def test_parse_service_name_timer():
    """Parse timer unit names the same way as service units."""
    project_group, suffix = parse_service_name("projects_claude-usage-notch-server_ping.timer")
    assert project_group == "claude-usage-notch-server"
    assert suffix == "ping"


@patch("src.services.subprocess.run")
def test_get_info_for_service(mock_run):
    """Return stdout on success, stdout+stderr on failure."""
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "Service is running"
    mock_run.return_value.stderr = ""
    assert get_info_for_service("test.service") == "Service is running"

    mock_run.return_value.returncode = 3
    mock_run.return_value.stdout = "Unit not found"
    mock_run.return_value.stderr = "Failed"
    assert get_info_for_service("test.service") == "Unit not found\nFailed"


@patch("src.services.get_info_for_service")
def test_get_service_status(mock_get_info):
    """Parse service status for active, failed, and inactive services."""
    mock_get_info.return_value = (
        "Active: active (running) since Mon; 4 days ago\n" "Memory: 123.4M\n" "CPU: 2min 15.678s\n"
    )
    status = get_service_status("projects_test.service")
    assert status.is_active and not status.is_failed
    assert status.uptime == "4d" and status.memory == "123.4M"
    assert status.project_group == "test"

    mock_get_info.return_value = "Active: failed (Result: exit-code)\nError: Connection refused\n"
    status = get_service_status("projects_test.service")
    assert not status.is_active and status.is_failed
    assert status.last_error == "Connection refused"

    mock_get_info.return_value = "Active: inactive (dead)\n"
    status = get_service_status("projects_test.service")
    assert not status.is_active and not status.is_failed

    mock_get_info.return_value = (
        "Active: active (waiting) since Mon 2025-06-09 10:00:00 UTC; 5 days ago\n"
        "Trigger: Thu 2025-06-19 10:00:00 UTC; 1 day 2h left\n"
    )
    status = get_service_status("projects_test_ping.timer")
    assert status.is_active and not status.is_failed
    assert status.uptime == "1d 2h"
    assert status.project_group == "test"


@pytest.mark.parametrize(
    "status_text,expected",
    [
        ("Trigger: Thu 2025-06-19 10:00:00 UTC; 1 day 2h left\n", "1d 2h"),
        ("Active: active (waiting) since Mon; 5 days ago\n", None),
    ],
)
def test_parse_timer_next(status_text, expected):
    """Parse time until next timer trigger."""
    assert parse_timer_next(status_text) == expected


@patch("src.services.requests.get")
def test_get_ci_status_success(mock_get):
    """Return success when latest workflow run succeeded."""
    mock_response = mock_get.return_value
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"workflow_runs": [{"conclusion": "success"}]}
    assert get_ci_status("test-repo") == "success"


@patch("src.services.requests.get")
def test_get_ci_status_failure(mock_get):
    """Return failure when latest workflow run failed."""
    mock_response = mock_get.return_value
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"workflow_runs": [{"conclusion": "failure"}]}
    assert get_ci_status("test-repo") == "failure"


@patch("src.services.requests.get")
def test_get_ci_status_no_runs(mock_get):
    """Return error when no workflow runs found."""
    mock_response = mock_get.return_value
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"workflow_runs": []}
    assert get_ci_status("test-repo") == "error"


@patch("src.services.requests.get")
def test_get_ci_status_other_conclusion(mock_get):
    """Return error for other conclusion values."""
    mock_response = mock_get.return_value
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"workflow_runs": [{"conclusion": "cancelled"}]}
    assert get_ci_status("test-repo") == "error"


@patch("src.services.requests.get")
def test_get_ci_status_request_exception(mock_get):
    """Return error on request exceptions."""
    import requests

    mock_get.side_effect = requests.RequestException("Connection error")
    assert get_ci_status("test-repo") == "error"


@patch("src.services.requests.get")
def test_get_ci_status_key_error(mock_get):
    """Return error on KeyError (missing workflow_runs key)."""
    mock_response = mock_get.return_value
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {}
    assert get_ci_status("test-repo") == "error"


@patch("src.services.requests.get")
def test_get_ci_status_with_token(mock_get):
    """Include Authorization header when token is available."""
    with patch("src.services.GITHUB_TOKEN", "ghp_test_token"):
        mock_response = mock_get.return_value
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"workflow_runs": [{"conclusion": "success"}]}
        get_ci_status("test-repo")
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "token ghp_test_token"


@patch("src.services.requests.get")
def test_get_ci_status_without_token(mock_get):
    """No Authorization header when token is None."""
    with patch("src.services.GITHUB_TOKEN", None):
        mock_response = mock_get.return_value
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"workflow_runs": [{"conclusion": "success"}]}
        get_ci_status("test-repo")
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["headers"] == {}


@patch("src.services.get_info_for_service")
@patch("src.services.get_ci_status")
def test_get_service_status_includes_ci(mock_get_ci, mock_get_info):
    """ServiceStatus includes CI status from API."""
    mock_get_info.return_value = "Active: active (running) since Mon; 4 days ago\n"
    mock_get_ci.return_value = "success"
    status = get_service_status("projects_test.service")
    assert status.ci_status == "success"
    mock_get_ci.assert_called_once_with("test")


@patch("src.services.requests.get")
def test_get_ci_status_uses_cache(mock_get):
    """CI status calls GitHub once within cache TTL."""
    mock_response = mock_get.return_value
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"workflow_runs": [{"conclusion": "success"}]}

    first = get_ci_status("test-repo")
    second = get_ci_status("test-repo")

    assert first == "success"
    assert second == "success"
    mock_get.assert_called_once()


@pytest.mark.parametrize(
    "uptime_text,expected",
    [
        ("532800.00 100.0", "6d 4h"),  # 6 days 4 hours
        ("3661.0 10.0", "1h 1m"),
        ("90.0 1.0", "1m"),
        ("5.0 0.0", "0m"),
    ],
)
def test_read_uptime(monkeypatch, uptime_text, expected):
    """Uptime is formatted to at most two compact units, days suppressing minutes."""
    monkeypatch.setattr(services_module.Path, "read_text", lambda self: uptime_text)
    assert services_module._read_uptime() == expected


def test_read_uptime_missing_file(monkeypatch):
    """Missing /proc/uptime yields None rather than raising."""

    def boom(self):
        raise OSError("no such file")

    monkeypatch.setattr(services_module.Path, "read_text", boom)
    assert services_module._read_uptime() is None


def test_read_memory(monkeypatch):
    """Memory is derived from MemTotal minus MemAvailable in /proc/meminfo."""
    meminfo = "MemTotal:        3979956 kB\nMemAvailable:    2049792 kB\nBuffers:           1 kB\n"
    monkeypatch.setattr(services_module.Path, "read_text", lambda self: meminfo)
    used_mb, total_mb, used_pct = services_module._read_memory()
    assert (used_mb, total_mb, used_pct) == (1885, 3887, 48.5)


def test_read_cpu_temperature(monkeypatch):
    """Thermal zone millidegrees are converted to Celsius."""
    monkeypatch.setattr(services_module.Path, "read_text", lambda self: "52600\n")
    assert services_module._read_cpu_temperature() == 52.6


def test_get_system_info_returns_dataclass():
    """get_system_info always returns a SystemInfo; cross-platform fields stay populated."""
    info = get_system_info()
    assert isinstance(info, SystemInfo)
    assert isinstance(info.hostname, str)
    assert info.cpu_count is None or info.cpu_count >= 1
