"""Pluggable AI backends. Pick one with LLM_PROVIDER; the rest of the bot doesn't care which.

    LLM_PROVIDER=auto        ANTHROPIC_API_KEY set -> anthropic; else `claude` on PATH -> claude-cli; else ollama
    LLM_PROVIDER=anthropic   Claude via API key
    LLM_PROVIDER=claude-cli  Claude via the local Claude Code CLI login
    LLM_PROVIDER=ollama      local model via Ollama (set LLM_MODEL, e.g. qwen2.5:7b)

To add a provider: write a class with `name`, `model` and `extract_json(system, user, schema)`,
then register it in `_BUILDERS` below.
"""

from __future__ import annotations

import os
import shutil
from typing import Callable

from .anthropic_api import AnthropicBackend
from .base import LLMBackend, LLMError
from .claude_cli import ClaudeCLIBackend
from .ollama import OllamaBackend

__all__ = ["LLMBackend", "LLMError", "get_backend", "PROVIDERS"]

_BUILDERS: dict[str, Callable[..., LLMBackend]] = {
    "anthropic": lambda model, env, timeout: AnthropicBackend(
        model=model, api_key=env.get("ANTHROPIC_API_KEY"), timeout=timeout),
    "claude-cli": lambda model, env, timeout: ClaudeCLIBackend(
        model=model, cli_path=env.get("CLAUDE_CLI_PATH") or "claude", timeout=timeout,
        extra_args=env.get("CLAUDE_CLI_EXTRA_ARGS", "")),
    "ollama": lambda model, env, timeout: OllamaBackend(
        model=model, host=env.get("OLLAMA_HOST") or "http://localhost:11434", timeout=timeout,
        keep_alive=env.get("OLLAMA_KEEP_ALIVE"), think=env.get("OLLAMA_THINK"),
        num_predict=env.get("OLLAMA_NUM_PREDICT"), temperature=env.get("OLLAMA_TEMPERATURE")),
}
PROVIDERS = ("auto", *_BUILDERS)


def _auto(env: dict[str, str]) -> str:
    if env.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if shutil.which(env.get("CLAUDE_CLI_PATH") or "claude"):
        return "claude-cli"
    return "ollama"


def get_backend(provider: str | None = None, model: str | None = None, env: dict[str, str] | None = None) -> LLMBackend:
    env = dict(os.environ) if env is None else env
    provider = (provider or env.get("LLM_PROVIDER") or "auto").strip().lower()
    if provider == "auto":
        provider = _auto(env)
    if provider not in _BUILDERS:
        raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(PROVIDERS)}")
    model = (model or env.get("LLM_MODEL") or "").strip() or None
    if model and model.lower() in PROVIDERS:
        raise LLMError(
            f"The model is set to '{model}', which is a provider name, not a model. "
            "Set the provider with LLM_PROVIDER and leave LLM_MODEL empty for the default "
            "(or a real model such as haiku / qwen2.5:7b). If .env looks right, check your shell: "
            "`echo $LLM_MODEL`, since exported variables take precedence over .env."
        )
    timeout = float(env.get("LLM_TIMEOUT_SECONDS") or 120)
    return _BUILDERS[provider](model, env, timeout)
