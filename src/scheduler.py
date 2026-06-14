import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import schedule

from src.services import get_service_status, get_services
from src.telegram import report_error_to_telegram

logger = logging.getLogger(__name__)

VALID_FREQUENCIES: frozenset[str] = frozenset({"muted", "hourly", "daily"})
ALERT_RESET_HOUR: int = 6  # AM reset for "daily" window
DEFAULT_ALERT_FREQUENCY: str = "hourly"

_SETTINGS_FILE = Path(__file__).parent.parent / "alert_settings.json"
_alert_settings: dict[str, str] = {}
_alerted_services: dict[str, datetime] = {}
_alert_lock = threading.Lock()


def _load_settings() -> None:
    if not _SETTINGS_FILE.exists():
        return
    try:
        data = json.loads(_SETTINGS_FILE.read_text())
        with _alert_lock:
            _alert_settings.update({k: v for k, v in data.items() if v in VALID_FREQUENCIES})
    except Exception:
        logger.warning("Failed to load alert settings from %s", _SETTINGS_FILE)


def _save_settings() -> None:
    try:
        with _alert_lock:
            data = dict(_alert_settings)
        tmp = _SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_SETTINGS_FILE)
    except Exception:
        logger.warning("Failed to save alert settings to %s", _SETTINGS_FILE)


_load_settings()


def get_alert_frequency(service_name: str, default: str = DEFAULT_ALERT_FREQUENCY) -> str:
    with _alert_lock:
        return _alert_settings.get(service_name, default)


def set_alert_frequency(service_name: str, frequency: str) -> None:
    """Set the alert frequency for a service. Raises ValueError for unknown frequencies."""
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"Invalid frequency: {frequency}")
    with _alert_lock:
        _alert_settings[service_name] = frequency
    _save_settings()


def get_all_alert_settings() -> dict[str, str]:
    """Return a snapshot of all explicitly configured alert settings."""
    with _alert_lock:
        return dict(_alert_settings)


def _alert_period_start(now: datetime) -> datetime:
    """Start of the current daily alert window (resets at ALERT_RESET_HOUR)."""
    if now.hour < ALERT_RESET_HOUR:
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=ALERT_RESET_HOUR, minute=0, second=0, microsecond=0)
    return now.replace(hour=ALERT_RESET_HOUR, minute=0, second=0, microsecond=0)


def _should_alert(service_name: str) -> bool:
    with _alert_lock:
        frequency = _alert_settings.get(service_name, DEFAULT_ALERT_FREQUENCY)
        if frequency == "muted":
            return False
        last_alert = _alerted_services.get(service_name)
        if last_alert is None:
            return True
        now = datetime.now()
        if frequency == "hourly":
            return (now - last_alert).total_seconds() >= 3600
        # daily: alert once per reset window
        return last_alert < _alert_period_start(now)


def _mark_alerted(service_name: str) -> None:
    with _alert_lock:
        _alerted_services[service_name] = datetime.now()


def service_health_check():
    """Check the health of services and send Telegram alerts for failures."""
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
                logger.info("Alert suppressed for %s (muted or already sent)", service_status.name)


def schedule_loop():
    """Schedule the periodic tasks."""
    schedule.every(5).minutes.do(service_health_check)
    logger.info("Scheduled 5-minute service health check")
    while True:
        schedule.run_pending()
        time.sleep(1)


def start_scheduler():
    """Start the background scheduler thread."""
    schedule_thread = threading.Thread(target=schedule_loop, daemon=True)
    schedule_thread.start()
    logger.info("Scheduler thread started")


if __name__ == "__main__":
    service_health_check()
