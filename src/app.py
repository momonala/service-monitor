import json
import logging
import queue
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, stream_with_context, url_for
from requests import RequestException

from src.backup_status import backup_statuses_for_groups
from src.canned_info import canned_service_statuses, canned_system_info, websites
from src.config import FLASK_PORT
from src.r2_usage import R2UsageSummary, fetch_r2_usage_summary
from src.scheduler import (
    DEFAULT_ALERT_FREQUENCY,
    VALID_FREQUENCIES,
    get_all_alert_settings,
    set_alert_frequency,
    start_scheduler,
)
from src.services import (
    ServiceStatus,
    get_info_for_service,
    get_service_health,
    get_service_status,
    get_services,
    get_system_info,
    is_linux,
)
from src.system_metrics import (
    DEFAULT_ROLLUP,
    DEFAULT_WINDOW,
    ROLLUPS,
    WINDOWS,
    history_payload,
    latest_service_sample_payload,
    latest_service_samples_payload,
    service_history_payload,
    temperature_window_stats,
)
from src.telegram import send_telegram_message
from src.values import INSPECTOR_DETECTOR_CWD, INSPECTOR_DETECTOR_UV_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

_base = Path(__file__).parent.parent
app = Flask(__name__, template_folder=str(_base / "templates"), static_folder=str(_base / "static"))
MAX_STATUS_WORKERS = 8


def _collect_statuses(
    services: list[str], read_status: Callable[[str], ServiceStatus]
) -> list[ServiceStatus]:
    """Fetch service statuses in parallel while preserving service order."""
    if not services:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_STATUS_WORKERS, len(services))) as pool:
        return list(pool.map(read_status, services))


def _collect_detailed_statuses(services: list[str]) -> list[ServiceStatus]:
    """Full status per service, including CI state, for the sidebar and backup views."""
    return _collect_statuses(services, lambda svc: get_service_status(svc, include_ci=True, status_lines=0))


def _validate_history_params(window: str, rollup: str) -> str | None:
    """Return an error message for an unusable window/rollup pair, or None when both are valid."""
    if window not in WINDOWS:
        return f"window must be one of: {', '.join(WINDOWS)}"
    if rollup not in ROLLUPS:
        return f"rollup must be one of: {', '.join(ROLLUPS)}"
    return None


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

    # `done` is consumed (and stripped from the URL) by the front-end toast.
    return redirect(url_for("index", service=service, done="restart"))


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

    return redirect(url_for("index", service=service, done="check"))


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
        line_queue: queue.Queue[str | None] = queue.Queue()

        def _reader():
            try:
                for line in proc.stdout:
                    line_queue.put(line)
            finally:
                line_queue.put(None)

        threading.Thread(target=_reader, daemon=True).start()
        try:
            while True:
                try:
                    line = line_queue.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                if line is None:
                    break
                yield f"data: {json.dumps(line.rstrip())}\n\n"
        except GeneratorExit:
            pass
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
    """Return enriched service details for sidebar rendering after first paint.

    Excludes backup status: that requires an `rclone lsjson` round-trip to R2 per project and
    can be noticeably slow, so it's served separately by `/api/services/backup-status` and the
    frontend fills in the backup icons whenever that resolves instead of blocking on it here.
    """
    if not is_linux():
        return jsonify({"services": []})

    services = get_services()
    detailed_statuses = _collect_detailed_statuses(services)
    latest_metrics = latest_service_samples_payload(services)
    payload = [
        {
            "name": status.name,
            "is_active": status.is_active,
            "is_failed": status.is_failed,
            "uptime": status.uptime,
            "last_error": status.last_error,
            "ci_status": status.ci_status,
            "project_group": status.project_group,
            # Stored samples are only refreshed while a unit is running, so a stopped
            # oneshot/backup service would otherwise keep showing its last-run reading forever.
            "cpu_percent": (
                latest_metrics.get(status.name, {}).get("cpu_percent") if status.is_active else None
            ),
            "memory_used_pct": (
                latest_metrics.get(status.name, {}).get("memory_used_pct") if status.is_active else None
            ),
            "memory_used_mb": (
                latest_metrics.get(status.name, {}).get("memory_used_mb") if status.is_active else None
            ),
        }
        for status in detailed_statuses
    ]
    return jsonify({"services": payload})


@app.route("/api/services/backup-status")
def services_backup_status():
    """Return per-project cloud-backup status, fetched separately from sidebar-details since
    it costs an `rclone lsjson` round-trip to R2 per project and shouldn't block the rest of
    the sidebar from rendering."""
    if not is_linux():
        return jsonify({"services": []})

    services = get_services()
    detailed_statuses = _collect_detailed_statuses(services)
    try:
        backup_by_group = backup_statuses_for_groups(
            sorted({status.project_group for status in detailed_statuses})
        )
    except Exception:
        logger.exception("Failed to compute backup status")
        backup_by_group = {}

    payload = []
    for status in detailed_statuses:
        # One backup icon per project, not per service — same rule as ci_status (services.py's
        # get_service_status only fetches CI for the suffix-less, non-timer unit).
        is_primary = status.suffix is None and not status.is_timer
        backup = backup_by_group.get(status.project_group) if is_primary else None
        if backup is None:
            continue
        payload.append(
            {
                "name": status.name,
                "backup_status": backup.status,
                "backup_stale_seconds": backup.stale_seconds,
                "backup_stale": backup.stale,
            }
        )
    return jsonify({"services": payload})


@app.route("/api/r2-usage")
def r2_usage():
    """Return this month's Cloudflare R2 usage (storage, Class A/B requests) against the free tier.

    `null` fields mean usage is unavailable (no Cloudflare token configured, or the API call
    failed) — the dashboard hides the row rather than showing a wrong number.
    """
    try:
        summary = fetch_r2_usage_summary()
    except Exception:
        logger.exception("Failed to fetch R2 usage")
        summary = None

    if summary is None:
        return jsonify({field.name: None for field in fields(R2UsageSummary)})
    return jsonify(asdict(summary))


@app.route("/api/system-info")
def system_info():
    """Return host (Raspberry Pi) vitals as JSON: temperature, CPU, memory, disk, uptime."""
    try:
        info = get_system_info() if is_linux() else canned_system_info
    except Exception:
        logger.exception("Failed to collect system info")
        return jsonify({"error": "failed to collect system info"}), 500
    payload = asdict(info)
    try:
        avg_24h, max_24h = temperature_window_stats(window="24h")
        payload["temperature_avg_24h"] = avg_24h
        payload["temperature_max_24h"] = max_24h
    except Exception:
        logger.exception("Failed to compute 24h temperature stats")
        payload["temperature_avg_24h"] = None
        payload["temperature_max_24h"] = None
    return jsonify(payload)


@app.route("/api/system-info/history")
def system_info_history():
    """Return windowed host vitals time series for the dashboard chart."""
    window = request.args.get("window", DEFAULT_WINDOW)
    rollup = request.args.get("rollup", DEFAULT_ROLLUP)
    error = _validate_history_params(window, rollup)
    if error:
        return jsonify({"error": error}), 400
    try:
        return jsonify(history_payload(window=window, rollup=rollup))
    except Exception:
        logger.exception("Failed to load system metrics history")
        return jsonify({"error": "failed to load system metrics history"}), 500


@app.route("/api/services/current")
def service_current():
    """Return one service's most recent CPU/memory reading for the live tiles on its detail view."""
    service = request.args.get("service", "").strip()
    if not service:
        return jsonify({"error": "service is required"}), 400
    if is_linux() and service not in get_services():
        return jsonify({"error": f"unknown service: {service}"}), 400
    try:
        payload = latest_service_sample_payload(service)
        if is_linux() and not get_service_health(service).is_active:
            payload["cpu_percent"] = None
            payload["memory_used_pct"] = None
            payload["memory_used_mb"] = None
        return jsonify(payload)
    except Exception:
        logger.exception("Failed to load current service metrics")
        return jsonify({"error": "failed to load current service metrics"}), 500


@app.route("/api/services/history")
def service_history():
    """Return one service's RAM/CPU time series for the per-service detail chart."""
    service = request.args.get("service", "").strip()
    window = request.args.get("window", DEFAULT_WINDOW)
    rollup = request.args.get("rollup", DEFAULT_ROLLUP)
    if not service:
        return jsonify({"error": "service is required"}), 400
    if is_linux() and service not in get_services():
        return jsonify({"error": f"unknown service: {service}"}), 400
    error = _validate_history_params(window, rollup)
    if error:
        return jsonify({"error": error}), 400
    try:
        return jsonify(service_history_payload(service, window=window, rollup=rollup))
    except Exception:
        logger.exception("Failed to load service metrics history")
        return jsonify({"error": "failed to load service metrics history"}), 500


@app.route("/")
def index():
    service = request.args.get("service")
    if is_linux():
        services = get_services()
        service_statuses = _collect_statuses(services, get_service_health)
    else:
        service_statuses = canned_service_statuses

    # lines=0: show only the systemctl status header (Active, Memory, CPU).
    # Log lines are streamed live via the /logs/stream SSE endpoint.
    selected_service_info = get_info_for_service(service, lines=0) if (service and is_linux()) else ""

    return render_template(
        "index.html",
        services=service_statuses,
        current=service,
        selected_service_info=selected_service_info,
        websites=websites,
    )


def main() -> None:
    start_scheduler()
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)


if __name__ == "__main__":
    main()
