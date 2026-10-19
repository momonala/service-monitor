"""Secrets loaded from .env (copy .env.example to .env for local development)."""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

TELEGRAM_API_TOKEN = os.environ.get("TELEGRAM_API_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
INSPECTOR_DETECTOR_UV_PATH = os.environ.get("INSPECTOR_DETECTOR_UV_PATH", "/home/mnalavadi/.local/bin/uv")
INSPECTOR_DETECTOR_CWD = os.environ.get("INSPECTOR_DETECTOR_CWD", "/home/mnalavadi/inspector_detector")
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_TOKEN = os.environ.get("CF_API_TOKEN") or dotenv_values(
    Path.home() / ".config" / "cloudflare" / "creds.env"
).get("CF_API_TOKEN", "")
