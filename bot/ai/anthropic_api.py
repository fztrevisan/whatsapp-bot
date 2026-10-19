"""Claude through the Anthropic API (needs ANTHROPIC_API_KEY). Fastest Claude option."""

from __future__ import annotations

from typing import Any

from .base import LLMError

DEFAULT_MODEL = "claude-haiku-4-5"
_TOOL = "record_event"


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float = 60.0, client: Any = None):
        self.model = model or DEFAULT_MODEL
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # optional dependency
                raise LLMError("The 'anthropic' package is not installed: pip install anthropic") from exc
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self._client = client

    def extract_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        # Forcing a single tool call is the most reliable way to get schema-shaped JSON from the API.
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                temperature=0,
                system=system,
                tools=[{"name": _TOOL, "description": "Record the extracted event details.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": _TOOL},
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise LLMError(f"Anthropic API call failed: {type(exc).__name__}: {exc}") from exc

        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and isinstance(block.input, dict):
                return block.input
        raise LLMError("Anthropic API returned no tool call")
