import logging
import threading
import time
from datetime import datetime, timedelta

import schedule

from src.services import get_service_status, get_services
from src.telegram import report_error_to_telegram

logger = logging.getLogger(__name__)

# Track which services have been alerted today: {service_name: last_alert_date}
_alerted_services: dict[str, datetime] = {}
_alert_lock = threading.Lock()
ALERT_RESET_HOUR: int = 6  # AM


def _get_alert_period_start() -> datetime:
    """Return the start of the current alert window (resets at ALERT_RESET_HOUR)."""
    now = datetime.now()
    if now.hour < ALERT_RESET_HOUR:
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=ALERT_RESET_HOUR, minute=0, second=0, microsecond=0)
    return now.replace(hour=ALERT_RESET_HOUR, minute=0, second=0, microsecond=0)


def _should_alert(service_name: str) -> bool:
    """Check if we should send an alert for this service (once per alert window)."""
    with _alert_lock:
        period_start = _get_alert_period_start()
        last_alert = _alerted_services.get(service_name)
        return last_alert is None or last_alert < period_start


def _mark_alerted(service_name: str) -> None:
    """Mark a service as alerted for the current alert window."""
    with _alert_lock:
        _alerted_services[service_name] = datetime.now()


def service_health_check():
    """Check the health of services and log their status."""
    services = get_services()
    service_statuses = [get_service_status(svc) for svc in services]
    for service_status in service_statuses:
        if service_status.is_failed:
            logger.warning("Service %s has failed.", service_status.name)
            if _should_alert(service_status.name):
                report_error_to_telegram(service_status)
                _mark_alerted(service_status.name)
                logger.info("Alert sent for %s", service_status.name)
            else:
                logger.info("Alert already sent today for %s, skipping.", service_status.name)


def schedule_loop():
    """Schedule the periodic tasks."""
    schedule.every().hour.at(":00").do(service_health_check)
    logger.info("Scheduled hourly service health check")
    while True:
        schedule.run_pending()
        time.sleep(1)


def start_threads():
    """Start the schedule thread."""
    schedule_thread = threading.Thread(target=schedule_loop, daemon=True)
    schedule_thread.start()
    logger.info("Initialized threads for schedule")


if __name__ == "__main__":
    service_health_check()
