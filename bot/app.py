"""Webhook receiver: OpenWA POSTs each new group message here; we reply to keywords.

Flow per message:
  1. verify HMAC signature, drop duplicates / other chats / own messages / non-text
  2. regex trigger (parse_command): not a command -> done, the message is dropped
  3. command -> answer OpenWA 200 immediately, then in the background run it (AI extraction for
     schedule-event) and send the reply through OpenWA

Message bodies are only held in memory while being processed. Nothing is logged or written to disk.
For schedule-event, the text after the keyword is sent to the configured AI provider.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
from collections import deque
from typing import Any, Callable

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from .ai import LLMBackend, get_backend
from .commands import Command, EventStore, Extractor, execute, parse_command
from .config import Settings, env_source
from .extraction import extract_event
from .openwa_client import OpenWAClient

log = logging.getLogger("openwa-bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def verify_signature(raw_body: bytes, header: str | None, secret: str) -> bool:
    if not header or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


class _RecentKeys:
    """Last N idempotency keys. OpenWA delivers at-least-once, so the same event can arrive twice."""

    def __init__(self, size: int = 500) -> None:
        self._order: deque[str] = deque(maxlen=size)
        self._set: set[str] = set()

    def seen(self, key: str) -> bool:
        if key in self._set:
            return True
        if len(self._order) == self._order.maxlen:
            self._set.discard(self._order[0])
        self._order.append(key)
        self._set.add(key)
        return False


def _author(data: dict[str, Any]) -> str:
    contact = data.get("contact") or {}
    name = contact.get("pushName") or contact.get("name") or "someone"
    return re.sub(r"[*_~`]", "", str(name))[:40] or "someone"


def _start_warm_up(backend: LLMBackend) -> None:
    """Load a local model in the background at startup, so the first event isn't a cold start."""
    warm_up = getattr(backend, "warm_up", None)
    if warm_up is None:
        return

    def run() -> None:
        log.info("Loading AI model '%s' in the background...", backend.model)
        try:
            log.info("AI model '%s' ready (loaded in %.1fs)", backend.model, warm_up())
        except Exception as exc:
            log.warning("AI model warm-up failed (the first event may be slow or fail): %s", exc)

    threading.Thread(target=run, name="llm-warm-up", daemon=True).start()


def create_app(
    settings: Settings | None = None,
    store: EventStore | None = None,
    send: Callable[[str, str, str | None], Any] | None = None,
    backend: LLMBackend | None = None,
    extract: Extractor | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or EventStore()
    recent = _RecentKeys()

    if send is None:
        missing = settings.missing()
        if missing:
            raise RuntimeError(f"Missing required settings: {', '.join(missing)} (see .env.example)")
        client = OpenWAClient(settings.openwa_url, settings.api_key)

        def send(chat_id: str, text: str, quoted_id: str | None) -> Any:
            return client.send_text(settings.session_id, chat_id, text, quoted_id)

    if extract is None:
        backend = backend or get_backend()
        log.info("AI provider: %s (model: %s) | LLM_PROVIDER from %s, LLM_MODEL from %s",
                 backend.name, backend.model, env_source("LLM_PROVIDER"), env_source("LLM_MODEL"))

        def extract(text: str):
            started = time.monotonic()
            try:
                return extract_event(text, backend, settings.now())
            finally:
                log.info("AI extraction took %.1fs", time.monotonic() - started)

        _start_warm_up(backend)

    app = FastAPI(title="OpenWA keyword bot", docs_url=None, redoc_url=None)
    app.state.store = store

    def _run(cmd: Command, author: str, chat_id: str, quoted_id: str | None) -> None:
        # Runs in a worker thread after OpenWA got its 200, so a slow model never times out the webhook.
        try:
            reply = execute(cmd, author, store, extract, settings.now())
            send(chat_id, reply, quoted_id)
            log.info("Handled '%s' command, reply sent to %s", cmd.kind, chat_id)
        except Exception as exc:  # never let a failure crash the worker
            log.error("Failed to handle '%s' command: %s", cmd.kind, exc)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "events": len(store.all()),
            "ai": {"provider": getattr(backend, "name", "custom"), "model": getattr(backend, "model", None)},
        }

    @app.post("/webhook")
    async def webhook(
        request: Request,
        background: BackgroundTasks,
        x_openwa_signature: str | None = Header(default=None),
        x_openwa_idempotency_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        raw = await request.body()
        if not verify_signature(raw, x_openwa_signature, settings.webhook_secret):
            raise HTTPException(status_code=401, detail="invalid signature")

        payload = await request.json()
        if payload.get("event") != "message.received":
            return {"status": "ignored"}
        if payload.get("sessionId") not in (None, settings.session_id):
            return {"status": "ignored"}
        if x_openwa_idempotency_key and recent.seen(x_openwa_idempotency_key):
            return {"status": "duplicate"}

        data: dict[str, Any] = payload.get("data") or {}
        chat_id = data.get("chatId") or data.get("from")
        if chat_id != settings.group_id:
            return {"status": "ignored"}
        if data.get("fromMe") and not settings.reply_to_self:
            return {"status": "ignored"}
        if data.get("type") not in (None, "text"):
            return {"status": "ignored"}

        cmd = parse_command(data.get("body"))
        if cmd is None:
            return {"status": "no-match"}

        background.add_task(_run, cmd, _author(data), chat_id, data.get("id"))
        return {"status": "accepted"}

    return app


def get_app() -> FastAPI:
    """Entry point for uvicorn:  uvicorn bot.app:get_app --factory"""
    return create_app()
