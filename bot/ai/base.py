"""The one interface every AI backend implements.

A backend takes a system prompt, a user prompt and a JSON Schema, and returns a dict that follows
the schema. That's all the bot needs, so swapping Claude for a local Ollama model (or adding a new
provider) never touches the bot logic.
"""

from __future__ import annotations

from typing import Any, Protocol


class LLMError(RuntimeError):
    """The model couldn't be reached or returned something unusable."""


class LLMBackend(Protocol):
    name: str   # provider id, e.g. "ollama"
    model: str  # model actually used, e.g. "qwen2.5:7b"

    def extract_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object matching `schema`. Raise LLMError on failure."""
        ...
