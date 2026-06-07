import logging

import requests

from src.services import ServiceStatus
from src.values import telegram_api_token, telegram_chat_id

logger = logging.getLogger(__name__)

MAX_STATUS_LENGTH = 4096 - 500  # Telegram limit is 4096, leave room for message template


def report_error_to_telegram(service_status: ServiceStatus) -> None:
    """Send an error message to a Telegram chat."""

    # Truncate full_status if too long - keep the END since errors are usually there
    full_status = service_status.full_status or "N/A"
    if len(full_status) > MAX_STATUS_LENGTH:
        full_status = "(truncated)...\n" + full_status[-MAX_STATUS_LENGTH:]

    message = f"""*Service:* `{_escape_markdown(service_status.name)}`
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

    url = f"https://api.telegram.org/bot{telegram_api_token}/sendMessage"
    payload = {
        "chat_id": telegram_chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, data=payload)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to send message to Telegram: %s", exc)


def _escape_markdown(text: str) -> str:
    """Escape special characters for Telegram Markdown."""
    for char in ["*", "`", "["]:
        text = text.replace(char, "\\" + char)
    return text
