"""Minimal client for the OpenWA REST API (only the calls this PoC needs)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx


class OpenWAClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{base_url}/api",
            headers={"X-API-Key": api_key},
            timeout=timeout,
            # A local gateway must never be routed through HTTP(S)_PROXY settings.
            trust_env=urlparse(base_url).hostname not in {"localhost", "127.0.0.1", "::1"},
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    # --- messaging -----------------------------------------------------------------------------
    def send_text(self, session_id: str, chat_id: str, text: str, quoted_message_id: str | None = None) -> Any:
        payload: dict[str, Any] = {"chatId": chat_id, "text": text, "linkPreview": False}
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
        return self._request("POST", f"/sessions/{session_id}/messages/send-text", json=payload)

    # --- setup helpers (used by scripts/setup_openwa.py) ---------------------------------------
    def list_sessions(self) -> Any:
        return self._request("GET", "/sessions")

    def create_session(self, name: str) -> Any:
        return self._request("POST", "/sessions", json={"name": name})

    def start_session(self, session_id: str) -> Any:
        return self._request("POST", f"/sessions/{session_id}/start")

    def get_session(self, session_id: str) -> Any:
        return self._request("GET", f"/sessions/{session_id}")

    def get_qr(self, session_id: str) -> Any:
        return self._request("GET", f"/sessions/{session_id}/qr")

    def list_groups(self, session_id: str) -> Any:
        return self._request("GET", f"/sessions/{session_id}/groups")

    def list_webhooks(self, session_id: str) -> Any:
        return self._request("GET", f"/sessions/{session_id}/webhooks")

    def delete_webhook(self, session_id: str, webhook_id: str) -> Any:
        return self._request("DELETE", f"/sessions/{session_id}/webhooks/{webhook_id}")

    def create_webhook(self, session_id: str, url: str, secret: str, group_id: str) -> Any:
        return self._request(
            "POST",
            f"/sessions/{session_id}/webhooks",
            json={
                "url": url,
                "events": ["message.received"],
                "secret": secret,
                "retryCount": 1,
                # Only this group's messages leave OpenWA — everything else is never sent to the bot.
                "filters": {"conditions": [{"field": "chatId", "operator": "is", "value": [group_id]}]},
            },
        )
