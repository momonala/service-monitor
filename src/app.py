import json
import logging
import os
import subprocess

from flask import Flask, Response, redirect, render_template, request, stream_with_context, url_for

from src.canned_info import canned_service_statuses, websites
from src.scheduler import start_threads
from src.services import get_info_for_service, get_service_status, get_services, is_linux

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)


@app.route("/restart", methods=["POST"])
def restart_service():
    """Trigger a service restart and immediately redirect back to the index view."""
    service = request.form.get("service", "")
    if not service:
        return "service parameter required", 400

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
    cmd = [
        "/home/mnalavadi/.local/bin/uv",
        "run",
        "-m",
        "scripts.check_inspections",
    ]

    try:
        result = subprocess.run(
            cmd, check=True, text=True, capture_output=True, cwd="/home/mnalavadi/inspector_detector"
        )
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


@app.route("/")
def index():
    service = request.args.get("service")
    if is_linux():
        services = get_services()
        service_statuses = [get_service_status(svc) for svc in services]
    else:
        service_statuses = canned_service_statuses

    # lines=0: show only the systemctl status header (Active, Memory, CPU).
    # Log lines are streamed live via the /logs/stream SSE endpoint.
    selected_service_info = get_info_for_service(service, lines=0) if service else ""

    return render_template(
        "index.html",
        services=service_statuses,
        current=service,
        selected_service_info=selected_service_info,
        websites=websites,
    )


def main():
    start_threads()
    app.run(host="0.0.0.0", port=5001, debug=False)


if __name__ == "__main__":
    main()
