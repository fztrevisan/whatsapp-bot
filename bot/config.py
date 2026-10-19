"""Settings read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import sys

from dotenv import dotenv_values, find_dotenv, load_dotenv

# .env is the source of truth for this project. By default python-dotenv lets variables already
# exported in the shell win, so a stale `export OPENWA_SESSION_ID=...` from an earlier run silently
# overrode .env. We load with override=True and warn about every shell value that gets replaced.
_DOTENV_PATH = find_dotenv(usecwd=True)
_DOTENV = {k: v for k, v in dotenv_values(_DOTENV_PATH).items() if v is not None} if _DOTENV_PATH else {}
_SHADOWED = sorted(k for k, v in _DOTENV.items() if k in os.environ and os.environ[k] != v)
if _SHADOWED:
    print(
        f"[config] .env overrides these variables exported in your shell: {', '.join(_SHADOWED)}. "
        f"Run `unset {' '.join(_SHADOWED)}` to silence this.",
        file=sys.stderr,
    )
load_dotenv(_DOTENV_PATH, override=True)


def env_source(name: str) -> str:
    """Where a variable's current value came from: '.env', 'shell' or 'unset'."""
    if not os.environ.get(name):
        return "unset"
    return ".env" if name in _DOTENV else "shell"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openwa_url: str
    api_key: str
    session_id: str
    group_id: str
    webhook_secret: str
    reply_to_self: bool
    timezone: str = ""  # IANA name for resolving "tomorrow" etc.; "" = this machine's timezone

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openwa_url=os.getenv("OPENWA_URL", "http://localhost:2785").rstrip("/"),
            api_key=os.getenv("OPENWA_API_KEY", ""),
            session_id=os.getenv("OPENWA_SESSION_ID", ""),
            group_id=os.getenv("WHATSAPP_GROUP_ID", ""),
            webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
            reply_to_self=_bool("REPLY_TO_SELF", False),
            timezone=os.getenv("EVENT_TIMEZONE", "").strip(),
        )

    def now(self) -> datetime:
        if self.timezone:
            return datetime.now(ZoneInfo(self.timezone))
        return datetime.now().astimezone()

    def missing(self) -> list[str]:
        required = {
            "OPENWA_API_KEY": self.api_key,
            "OPENWA_SESSION_ID": self.session_id,
            "WHATSAPP_GROUP_ID": self.group_id,
            "WEBHOOK_SECRET": self.webhook_secret,
        }
        return [k for k, v in required.items() if not v]
