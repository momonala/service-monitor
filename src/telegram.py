"""Telegram alerting: one transport path for custom and service-failure messages."""

import requests

from src.services import ServiceStatus
from src.values import telegram_api_token, telegram_chat_id

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
# Leave room in the failure template for fields around full_status.
_MAX_FULL_STATUS_LENGTH = TELEGRAM_MAX_MESSAGE_LENGTH - 500


def send_telegram_message(text: str) -> None:
    """Send a Markdown message to the configured Telegram chat.

    Raises:
        ValueError: if ``text`` is empty or whitespace-only.
        requests.RequestException: if the Telegram API request fails.
    """
    if not text or not text.strip():
        raise ValueError("message must be non-empty")

    payload = {
        "chat_id": telegram_chat_id,
        "text": _fit_telegram_length(text),
        "parse_mode": "Markdown",
    }
    response = requests.post(
        f"https://api.telegram.org/bot{telegram_api_token}/sendMessage",
        data=payload,
    )
    response.raise_for_status()


def send_service_failure_alert(service_status: ServiceStatus) -> None:
    """Format and send a failed-service alert. Raises on transport failure."""
    send_telegram_message(_format_service_failure_alert(service_status))


def _format_service_failure_alert(service_status: ServiceStatus) -> str:
    full_status = _truncate_keeping_end(
        service_status.full_status or "N/A",
        _MAX_FULL_STATUS_LENGTH,
    )
    return f"""*Service:* `{_escape_markdown(service_status.name)}`
*Last Error:* `{_escape_markdown(service_status.last_error or 'N/A')}`
*Is Active:* `{service_status.is_active}`
*Is Failed:* `{service_status.is_failed}`
*Uptime:* `{_escape_markdown(service_status.uptime or 'N/A')}`
*Memory:* `{_escape_markdown(service_status.memory or 'N/A')}`
*CPU:* `{_escape_markdown(service_status.cpu or 'N/A')}`

*Full Status:*
```
{full_status}
```"""


def _fit_telegram_length(text: str) -> str:
    if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return text
    return text[: TELEGRAM_MAX_MESSAGE_LENGTH - 20] + "\n...(truncated)"


def _truncate_keeping_end(text: str, max_length: int) -> str:
    """Keep the end of ``text`` (errors usually appear late in systemctl status)."""
    if len(text) <= max_length:
        return text
    return "(truncated)...\n" + text[-max_length:]


def _escape_markdown(text: str) -> str:
    """Escape special characters for Telegram legacy Markdown."""
    for char in ["*", "`", "["]:
        text = text.replace(char, "\\" + char)
    return text
