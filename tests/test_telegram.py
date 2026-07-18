"""Tests for telegram alert formatting and transport."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.services import ServiceStatus
from src.telegram import TELEGRAM_MAX_MESSAGE_LENGTH, send_service_failure_alert, send_telegram_message


def _failed_status(**overrides) -> ServiceStatus:
    base = {
        "name": "projects_foo.service",
        "is_active": False,
        "is_failed": True,
        "uptime": "1h",
        "memory": "10M",
        "cpu": "1s",
        "last_error": "boom *not* escaped yet",
        "full_status": "ok so far",
        "project_group": "foo",
        "suffix": None,
        "ci_status": None,
    }
    base.update(overrides)
    return ServiceStatus(**base)


def test_send_telegram_message_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        send_telegram_message("   ")


@patch("src.telegram.requests.post")
def test_send_telegram_message_posts_markdown(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=MagicMock())
    send_telegram_message("*hello*")
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["text"] == "*hello*"
    assert kwargs["data"]["parse_mode"] == "Markdown"


@patch("src.telegram.requests.post")
def test_send_telegram_message_truncates_overlong(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=MagicMock())
    send_telegram_message("x" * (TELEGRAM_MAX_MESSAGE_LENGTH + 50))
    text = mock_post.call_args.kwargs["data"]["text"]
    assert len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH
    assert text.endswith("...(truncated)")


@patch("src.telegram.send_telegram_message")
def test_send_service_failure_alert_formats_then_sends(mock_send):
    send_service_failure_alert(_failed_status(full_status="line1\nerror at end"))
    message = mock_send.call_args.args[0]
    assert "*Service:* `projects_foo.service`" in message
    assert "boom \\*not\\* escaped yet" in message
    assert "error at end" in message


@patch("src.telegram.send_telegram_message")
def test_send_service_failure_alert_keeps_end_of_long_status(mock_send):
    long_status = "START" + ("." * 5000) + "TAIL_ERROR"
    send_service_failure_alert(_failed_status(full_status=long_status))
    message = mock_send.call_args.args[0]
    assert "TAIL_ERROR" in message
    assert "(truncated)" in message
    assert "START" not in message


@patch("src.telegram.requests.post", side_effect=requests.RequestException("down"))
def test_send_telegram_message_propagates_transport_errors(mock_post):
    with pytest.raises(requests.RequestException):
        send_telegram_message("ping")
