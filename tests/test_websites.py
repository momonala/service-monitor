"""Tests for building project links from the tunnel config."""

from pathlib import Path

from src.websites import project_websites, read_tunnel_routes


def test_read_tunnel_routes_keeps_only_local_http_rules(tmp_path: Path):
    config = tmp_path / "config.yml"
    config.write_text(
        "tunnel: t\n"
        "ingress:\n"
        "  - hostname: ssh.example.org\n"
        "    service: ssh://localhost:22\n"
        "  - hostname: a.example.org\n"
        "    service: http://localhost:5002\n"
        "  - service: http_status:404\n"
    )

    assert read_tunnel_routes(config) == [("a.example.org", 5002)]


def test_project_websites_one_link_per_live_project_port_labelled_by_hostname():
    routes = [
        ("servicemonitor.example.org", 5001),
        ("inspectordetector.example.org", 5002),
        ("trace.example.org", 5003),
        ("incognita.example.org", 5004),
        ("whats-on-the-menu.example.org", 5011),
        ("find-my.example.org", 5016),
        ("my-cloud.example.org", 5016),
        ("other.example.org", 3000),
    ]
    listening = {
        5001: "projects_service-monitor.service",
        5002: "projects_inspector-detector_site.service",
        5003: "projects_incognita_data-api.service",
        5004: "projects_incognita_dashboard.service",
        5016: "projects_my-cloud.service",
        3000: "grafana-server.service",
    }

    assert project_websites(routes, listening, own_port=5001) == [
        {"name": "incognita", "project": "incognita", "url": "https://incognita.example.org"},
        {
            "name": "inspectordetector",
            "project": "inspector-detector",
            "url": "https://inspectordetector.example.org",
        },
        {"name": "my-cloud", "project": "my-cloud", "url": "https://my-cloud.example.org"},
        {"name": "trace", "project": "incognita", "url": "https://trace.example.org"},
    ]
