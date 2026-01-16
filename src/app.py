import logging
import os
import subprocess

from flask import Flask, redirect, render_template, request, url_for

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
    """Restart a given service and redirect back to the index view."""
    service = request.form.get("service", "")
    try:
        # Requires appropriate sudoers configuration for the running user
        subprocess.run(["sudo", "systemctl", "restart", service], check=True, text=True, capture_output=True)
        logger.info("Successfully restarted service %s", service)
    except subprocess.CalledProcessError as exc:
        logger.error("Failed to restart %s: %s", service, exc.stderr)
        return (exc.stderr or f"Failed to restart {service}"), 500

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


@app.route("/")
def index():
    service = request.args.get("service")
    if is_linux():
        services = get_services()
        service_statuses = [get_service_status(svc) for svc in services]
    else:
        service_statuses = canned_service_statuses

    # Get detailed info for selected service if one is selected
    selected_service_info = get_info_for_service(service) if service else ""

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
