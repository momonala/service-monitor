import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, stream_with_context, url_for
from requests import RequestException

from src.canned_info import canned_service_statuses, canned_system_info, websites
from src.scheduler import (
    DEFAULT_ALERT_FREQUENCY,
    VALID_FREQUENCIES,
    get_all_alert_settings,
    set_alert_frequency,
    start_scheduler,
)
from src.services import (
    aggregate_project_resources,
    get_info_for_service,
    get_service_health,
    get_service_status,
    get_services,
    get_system_info,
    is_linux,
    parse_service_name,
)
from src.telegram import send_telegram_message
from src.values import INSPECTOR_DETECTOR_CWD, INSPECTOR_DETECTOR_UV_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

_base = Path(__file__).parent.parent
app = Flask(__name__, template_folder=str(_base / "templates"), static_folder=str(_base / "static"))
MAX_STATUS_WORKERS = 8


def _collect_statuses(services: list[str], detailed: bool) -> list:
    """Fetch service statuses in parallel while preserving service order."""
    if not services:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_STATUS_WORKERS, len(services))) as pool:
        if detailed:
            return list(
                pool.map(lambda svc: get_service_status(svc, include_ci=True, status_lines=0), services)
            )
        return list(pool.map(get_service_health, services))


@app.route("/restart", methods=["POST"])
def restart_service():
    """Trigger a service restart and immediately redirect back to the index view."""
    service = request.form.get("service", "")
    if not service:
        return "service parameter required", 400

    if is_linux():
        known = get_services(use_cache=True)
        if service not in known:
            logger.warning("Restart requested for unknown service: %s", service)
            return f"Unknown service: {service}", 400

    try:
        # Requires appropriate sudoers configuration for the running user
        subprocess.Popen(
            ["sudo", "systemctl", "restart", service],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        logger.info("Triggered restart for service %s", service)
    except OSError as exc:
        logger.error("Failed to trigger restart for %s: %s", service, exc)
        return f"Failed to trigger restart for {service}", 500

    return redirect(url_for("index", service=service))


@app.route("/inspector-detector/check", methods=["POST"])
def inspector_detector_check():
    """Run the Inspector Detector inspections check command."""
    service = request.form.get("service", "")
    cmd = [INSPECTOR_DETECTOR_UV_PATH, "run", "-m", "scripts.check_inspections"]

    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True, cwd=INSPECTOR_DETECTOR_CWD)
        logger.info("inspector-detector check completed. stdout: %s", (result.stdout or "").strip())
        if result.stderr:
            logger.warning("inspector-detector check stderr: %s", result.stderr.strip())
    except subprocess.CalledProcessError as exc:
        logger.error("inspector-detector check failed: %s", exc.stderr)
        return (exc.stderr or "inspector-detector check failed"), 500

    return redirect(url_for("index", service=service))


@app.route("/logs/stream")
def stream_logs():
    """SSE endpoint that tails journalctl for a given service."""
    service = request.args.get("service", "")
    if not service:
        return "service parameter required", 400

    def generate():
        if not is_linux():
            yield "data: [Log streaming is only available on Linux]\n\n"
            return

        proc = subprocess.Popen(
            ["journalctl", "-u", service, "-f", "-n", "500", "--no-pager", "--output=short-iso"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for line in proc.stdout:
                yield f"data: {json.dumps(line.rstrip())}\n\n"
        finally:
            proc.terminate()
            proc.wait()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/alert", methods=["POST"])
def send_alert():
    """Send a custom Telegram alert. Expects JSON {message} with Telegram Markdown."""
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"ok": False, "error": "message parameter required"}), 400

    try:
        send_telegram_message(message)
    except RequestException:
        logger.exception("Failed to send custom Telegram alert")
        return jsonify({"ok": False, "error": "failed to send alert"}), 502

    return jsonify({"ok": True})


@app.route("/api/alert-settings")
def get_alert_settings():
    """Return alert frequency settings for all known services."""
    services = get_services(use_cache=True) if is_linux() else []
    settings = get_all_alert_settings()
    return jsonify({svc: settings.get(svc, DEFAULT_ALERT_FREQUENCY) for svc in services})


@app.route("/api/alert-settings", methods=["POST"])
def update_alert_setting():
    """Update the alert frequency for a single service. Expects JSON {service, frequency}."""
    data = request.get_json(silent=True) or {}
    service = data.get("service", "")
    frequency = data.get("frequency", "")
    if not service or frequency not in VALID_FREQUENCIES:
        return "Invalid request", 400
    set_alert_frequency(service, frequency)
    return jsonify({"ok": True})


@app.route("/api/services/sidebar-details")
def sidebar_details():
    """Return enriched service details for sidebar rendering after first paint."""
    if not is_linux():
        project_resources = aggregate_project_resources(canned_service_statuses)
        return jsonify(
            {
                "services": [],
                "projects": {
                    project_group: {"memory": resources.memory, "cpu": resources.cpu}
                    for project_group, resources in project_resources.items()
                },
            }
        )

    services = get_services()
    detailed_statuses = _collect_statuses(services, detailed=True)
    project_resources = aggregate_project_resources(detailed_statuses)
    payload = [
        {
            "name": status.name,
            "is_active": status.is_active,
            "is_failed": status.is_failed,
            "uptime": status.uptime,
            "memory": status.memory,
            "cpu": status.cpu,
            "last_error": status.last_error,
            "ci_status": status.ci_status,
        }
        for status in detailed_statuses
    ]
    projects = {
        project_group: {"memory": resources.memory, "cpu": resources.cpu}
        for project_group, resources in project_resources.items()
    }
    return jsonify({"services": payload, "projects": projects})


@app.route("/api/system-info")
def system_info():
    """Return host (Raspberry Pi) vitals as JSON: temperature, CPU, memory, disk, uptime."""
    try:
        info = get_system_info() if is_linux() else canned_system_info
    except Exception:
        logger.exception("Failed to collect system info")
        return jsonify({"error": "failed to collect system info"}), 500
    return jsonify(asdict(info))


@app.route("/")
def index():
    service = request.args.get("service")
    if is_linux():
        services = get_services()
        service_statuses = _collect_statuses(services, detailed=False)
    else:
        service_statuses = canned_service_statuses

    # lines=0: show only the systemctl status header (Active, Memory, CPU).
    # Log lines are streamed live via the /logs/stream SSE endpoint.
    selected_service_info = get_info_for_service(service, lines=0) if (service and is_linux()) else ""
    current_project_group = parse_service_name(service)[0] if service else None

    return render_template(
        "index.html",
        services=service_statuses,
        current=service,
        current_project_group=current_project_group,
        selected_service_info=selected_service_info,
        websites=websites,
    )


def main():
    start_scheduler()
    app.run(host="0.0.0.0", port=5001, debug=False)


if __name__ == "__main__":
    main()
