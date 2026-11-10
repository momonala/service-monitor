"""Tests for services.py module."""

import subprocess
from unittest.mock import patch

import pytest

import src.services as services_module
from src.services import (
    ServiceCounters,
    SystemInfo,
    get_ci_status,
    get_services,
    get_system_info,
    parse_cpu,
    parse_last_error,
    parse_memory,
    parse_service_name,
    parse_uptime,
    read_service_metrics,
    sum_io_stat_written_bytes,
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
        ("Active: active (running) since Mon; 2h 15min ago", "2h"),
        ("Active: inactive (dead) since Mon; 4 days ago", None),
    ],
)
def test_parse_uptime(status_text, expected):
    """Parse uptime extracts duration from active services only."""
    assert parse_uptime(status_text) == expected


@pytest.mark.parametrize(
    "parser, status_text, expected",
    [
        (parse_memory, "Memory: 1.2G\nCPU: 100ms", "1.2G"),
        (parse_cpu, "CPU: 1h 23min 45.678s\nMemory: 100M", "1h 23min 45.678s"),
        (parse_last_error, "Error: Command returned exit code 1\nOther info", "Command returned exit code 1"),
        (parse_last_error, "No errors here", None),
    ],
)
def test_parse_status_fields(parser, status_text, expected):
    assert parser(status_text) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("projects_energy-monitor.service", ("energy-monitor", None)),
        (
            "projects_energy-monitor_data-backup-scheduler.service",
            ("energy-monitor", "data-backup-scheduler"),
        ),
        ("projects_claude-usage-notch-server_ping.timer", ("claude-usage-notch-server", "ping")),
    ],
)
def test_parse_service_name(name, expected):
    assert parse_service_name(name) == expected


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


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"workflow_runs": [{"conclusion": "success"}]}, "success"),
        ({"workflow_runs": [{"conclusion": "failure"}]}, "failure"),
        ({"workflow_runs": [{"conclusion": "cancelled"}]}, "error"),
        ({"workflow_runs": []}, "error"),
    ],
)
@patch("src.services.requests.get")
def test_get_ci_status_maps_latest_run(mock_get, body, expected):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = body
    assert get_ci_status("test-repo") == expected


@patch("src.services.requests.get")
def test_get_ci_status_missing_workflow_is_none_and_cached(mock_get):
    """Repos without a GitHub Actions workflow get no badge, and are not re-queried within the TTL."""
    mock_response = mock_get.return_value
    mock_response.status_code = 404

    assert get_ci_status("vbb-rest") is None
    assert get_ci_status("vbb-rest") is None
    mock_get.assert_called_once()


@patch("src.services.requests.get")
def test_get_ci_status_request_exception(mock_get):
    """Return error on request exceptions."""
    import requests

    mock_get.side_effect = requests.RequestException("Connection error")
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
        ("532800.00 100.0", "6d"),  # 6 days 4 hours
        ("3661.0 10.0", "1h"),
        ("90.0 1.0", "1m"),
    ],
)
def test_read_uptime(monkeypatch, uptime_text, expected):
    """Uptime is formatted to a single compact unit."""
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


def test_read_service_metrics_parses_blocks(monkeypatch):
    """Each unit's block is parsed into ServiceCounters; a missing cgroup leaves cgroup reads None."""
    stdout = (
        "Id=projects_foo.service\nControlGroup=/system.slice/projects_foo.service\nCPUUsageNSec=1500000000\n"
        "\n"
        "Id=projects_bar.service\nControlGroup=\nCPUUsageNSec=42\n"
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(services_module.subprocess, "run", lambda *a, **k: completed)
    monkeypatch.setattr(services_module, "_read_cgroup_anon", lambda path: 209715200)
    monkeypatch.setattr(services_module, "read_cgroup_written_bytes", lambda path: 4096)
    metrics = read_service_metrics(["projects_foo.service", "projects_bar.service"])
    assert metrics["projects_foo.service"] == ServiceCounters(209715200, 1500000000, 4096)
    assert metrics["projects_bar.service"] == ServiceCounters(None, 42, None)  # empty ControlGroup


def test_read_service_metrics_unit_with_no_block_gets_none(monkeypatch):
    """A unit whose type emits no properties (e.g. some .timer units) reports empty counters, not
    another unit's data — this is the misattribution regression test."""
    stdout = "Id=projects_bar.service\nControlGroup=/system.slice/projects_bar.service\nCPUUsageNSec=99\n"
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(services_module.subprocess, "run", lambda *a, **k: completed)
    monkeypatch.setattr(services_module, "_read_cgroup_anon", lambda path: 1024)
    monkeypatch.setattr(services_module, "read_cgroup_written_bytes", lambda path: 7)
    metrics = read_service_metrics(["projects_foo.timer", "projects_bar.service"])
    assert metrics["projects_foo.timer"] == ServiceCounters()
    assert metrics["projects_bar.service"] == ServiceCounters(1024, 99, 7)


def test_read_service_metrics_empty_units_skips_subprocess(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("subprocess should not run for empty units")

    monkeypatch.setattr(services_module.subprocess, "run", _boom)
    assert read_service_metrics([]) == {}


def test_read_service_metrics_returns_empty_on_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="boom")

    monkeypatch.setattr(services_module.subprocess, "run", fail)
    assert read_service_metrics(["projects_foo.service"]) == {}


def test_sum_io_stat_written_bytes_adds_every_device():
    io_stat = (
        "179:0 rbytes=385024 wbytes=23601152 rios=40 wios=2025 dbytes=0 dios=0\n"
        "8:0 rbytes=0 wbytes=1000 rios=0 wios=1 dbytes=0 dios=0\n"
    )
    assert sum_io_stat_written_bytes(io_stat) == 23602152


def test_parse_journal_entries_counts_per_unit_and_keeps_the_last_cursor():
    lines = "\n".join(
        [
            '{"__CURSOR":"c1","_SYSTEMD_UNIT":"projects_a.service"}',
            '{"__CURSOR":"c2","_SYSTEMD_UNIT":"projects_a.service"}',
            '{"__CURSOR":"c3"}',
            "not json",
        ]
    )
    assert services_module.parse_journal_entries(lines) == ({"projects_a.service": 2, "": 1}, "c3")


def test_read_journal_entries_reports_a_failed_journalctl_as_none(monkeypatch):
    failed = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="Failed to seek to cursor")
    monkeypatch.setattr(services_module.subprocess, "run", lambda *args, **kwargs: failed)
    assert services_module.read_journal_entries("stale", since_ts=0.0) is None
