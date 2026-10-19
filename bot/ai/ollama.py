"""Local open-weight models through Ollama (Qwen, Llama, Gemma...). Nothing leaves the machine.

Cold starts: Ollama unloads a model after 5 idle minutes by default, and reloading a 7B model can
take tens of seconds (minutes on CPU-only machines). To keep group replies fast we:
  - send `keep_alive` with every request so the model stays in memory between messages;
  - expose warm_up(), which the bot calls once at startup to load the model before the first event.

Thinking models (qwen3, gemma4, deepseek-r1...): Ollama runs their reasoning pass by default when
`think` isn't sent. For extraction that only adds latency, and on some inputs the reasoning runs on
(or loops) until the timeout. So we send `think: false` and cap the output with `num_predict`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .base import LLMError

log = logging.getLogger("openwa-bot")

DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_KEEP_ALIVE = "30m"
DEFAULT_NUM_PREDICT = 512  # the event JSON is ~100-200 tokens
DEFAULT_TEMPERATURE = 0.0
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_MODEL_DEFAULT = "default"  # OLLAMA_THINK / OLLAMA_TEMPERATURE value meaning "use the model's own setting"


def _parse_keep_alive(value: str | None) -> str | int:
    """Ollama accepts a duration ("30m", "2h") or a number of seconds (-1 = keep forever)."""
    value = (value or DEFAULT_KEEP_ALIVE).strip()
    try:
        return int(value)
    except ValueError:
        return value


def _parse_think(value: str | None) -> bool | str | None:
    """false (default) / true / "default" (None: don't send `think`) / a level such as "high"."""
    value = (value or "false").strip().lower()
    if value in {"false", "0", "no", "off"}:
        return False
    if value in {"true", "1", "yes", "on"}:
        return True
    return None if value == _MODEL_DEFAULT else value


def _parse_num_predict(value: str | None) -> int:
    """Max tokens to generate (thinking included). -1 = no limit."""
    value = (value or "").strip()
    try:
        return int(value) if value else DEFAULT_NUM_PREDICT
    except ValueError as exc:
        raise LLMError(f"OLLAMA_NUM_PREDICT must be an integer, got '{value}'") from exc


def _parse_temperature(value: str | None) -> float | None:
    """A number, or "default" (None: use the temperature from the model's Modelfile)."""
    value = (value or "").strip().lower()
    if value == _MODEL_DEFAULT:
        return None
    try:
        return float(value) if value else DEFAULT_TEMPERATURE
    except ValueError as exc:
        raise LLMError(f"OLLAMA_TEMPERATURE must be a number or '{_MODEL_DEFAULT}', got '{value}'") from exc


class OllamaBackend:
    name = "ollama"

    def __init__(self, model: str | None = None, host: str = "http://localhost:11434", timeout: float = 120.0,
                 keep_alive: str | None = None, think: str | None = None, num_predict: str | None = None,
                 temperature: str | None = None, transport: httpx.BaseTransport | None = None):
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.keep_alive = _parse_keep_alive(keep_alive)
        self.think = _parse_think(think)
        self.num_predict = _parse_num_predict(num_predict)
        self.temperature = _parse_temperature(temperature)
        host = host.rstrip("/")
        # A local Ollama must never go through HTTP(S)_PROXY settings (common on corporate PCs).
        self.trust_env = urlparse(host).hostname not in _LOCAL_HOSTS
        self._http = httpx.Client(base_url=host, timeout=timeout, transport=transport, trust_env=self.trust_env)

    def _post(self, payload: dict[str, Any], timeout: float) -> httpx.Response:
        started = time.monotonic()
        try:
            return self._http.post("/api/chat", json=payload, timeout=timeout)
        except httpx.ConnectError as exc:
            raise LLMError(f"Can't reach Ollama at {self._http.base_url} (is `ollama serve` running?): {exc}") from exc
        except httpx.ConnectTimeout as exc:
            raise LLMError(f"Connecting to Ollama at {self._http.base_url} timed out") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(self._timeout_message(time.monotonic() - started)) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {type(exc).__name__}: {exc}") from exc

    def _is_loaded(self) -> bool | None:
        """Is the model in Ollama's memory right now (GET /api/ps)? None if we can't tell."""
        want = self.model if ":" in self.model else f"{self.model}:latest"
        try:
            models = self._http.get("/api/ps", timeout=3).json().get("models") or []
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return any(want in (m.get("name"), m.get("model")) for m in models)

    def _timeout_message(self, elapsed: float) -> str:
        head = f"Ollama didn't answer within {elapsed:.0f}s."
        loaded = self._is_loaded()
        if loaded is False:
            return (f"{head} Model '{self.model}' isn't in memory, so it was probably still loading. Wait for the "
                    "'model ready' log line, raise LLM_TIMEOUT_SECONDS, or set OLLAMA_KEEP_ALIVE=-1.")
        if loaded is None:
            return (f"{head} Model '{self.model}' may still be loading, or the machine is too slow for it. "
                    "Raise LLM_TIMEOUT_SECONDS or use a smaller model.")
        hints = []
        if self.think is not False:
            current = {True: "true", None: _MODEL_DEFAULT}.get(self.think, self.think)
            hints.append(f"thinking is on (OLLAMA_THINK={current}), set OLLAMA_THINK=false")
        if self.num_predict < 0:
            hints.append("output length is unlimited (OLLAMA_NUM_PREDICT=-1), set a limit such as 512")
        hints.append("raise LLM_TIMEOUT_SECONDS or use a smaller model")
        return (f"{head} Model '{self.model}' is loaded, so generating the answer itself was too slow: "
                + "; ".join(hints) + ".")

    def warm_up(self, timeout: float = 600.0) -> float:
        """Load the model into memory (an empty chat request only loads it). Returns seconds taken."""
        started = time.monotonic()
        resp = self._post({"model": self.model, "messages": [], "keep_alive": self.keep_alive}, timeout=timeout)
        self._raise_for_status(resp)
        return time.monotonic() - started

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise LLMError(f"Ollama model '{self.model}' not found. Run: ollama pull {self.model}")
        if resp.status_code >= 400:
            raise LLMError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")

    def extract_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,          # Ollama structured outputs: constrains decoding to this JSON Schema
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": self.num_predict},
        }
        if self.think is not None:
            payload["think"] = self.think
        if self.temperature is not None:
            payload["options"]["temperature"] = self.temperature
        resp = self._post(payload, timeout=self.timeout)
        self._raise_for_status(resp)
        try:
            body = resp.json()
            message = body["message"]
        except (KeyError, ValueError) as exc:
            raise LLMError("Ollama returned an invalid response") from exc
        thinking = message.get("thinking") or ""
        log.info("Ollama stats: load %.1fs, prompt %s tokens, output %s tokens (%d chars of thinking), done_reason=%s",
                 (body.get("load_duration") or 0) / 1e9, body.get("prompt_eval_count"), body.get("eval_count"),
                 len(thinking), body.get("done_reason"))
        if body.get("done_reason") == "length":
            hint = " Most of it was thinking, set OLLAMA_THINK=false." if thinking else ""
            raise LLMError(f"Model '{self.model}' hit the OLLAMA_NUM_PREDICT limit ({self.num_predict} tokens) "
                           f"before finishing its answer.{hint}")
        try:
            data = json.loads(message["content"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("Ollama returned an invalid response") from exc
        if not isinstance(data, dict):
            raise LLMError("Ollama returned JSON that isn't an object")
        return data
