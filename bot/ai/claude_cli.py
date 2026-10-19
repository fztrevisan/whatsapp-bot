"""Claude through the local Claude Code CLI (`claude -p`). Uses your existing Claude login, no API key.

Each call runs a one-shot, tool-less, non-persisted CLI session:
  --json-schema             validated structured output (returned as `structured_output`)
  --tools ""                no file/shell tools: the model can only answer
  --strict-mcp-config       don't start any MCP servers (faster, nothing extra exposed)
  --no-session-persistence  no transcript written to ~/.claude, so the message isn't kept
The prompt goes through stdin and the process runs in an empty temp dir, so no project files
or CLAUDE.md from this repo are picked up.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from typing import Any

from .base import LLMError

DEFAULT_MODEL = "haiku"


class ClaudeCLIBackend:
    name = "claude-cli"

    def __init__(self, model: str | None = None, cli_path: str = "claude", timeout: float = 120.0, extra_args: str = ""):
        self.model = model or DEFAULT_MODEL
        resolved = shutil.which(cli_path)
        if not resolved:
            raise LLMError(f"Claude Code CLI not found ('{cli_path}'). Install it or set CLAUDE_CLI_PATH.")
        self._cli = resolved
        self._timeout = timeout
        self._extra = shlex.split(extra_args) if extra_args else []

    def _command(self, system: str, schema: dict[str, Any]) -> list[str]:
        return [
            self._cli, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--system-prompt", system,
            "--model", self.model,
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            *self._extra,
        ]

    def extract_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="openwa-bot-") as cwd:
            try:
                proc = subprocess.run(
                    self._command(system, schema),
                    input=user, capture_output=True, text=True, timeout=self._timeout, cwd=cwd,
                )
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"Claude CLI timed out after {self._timeout:.0f}s") from exc

        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = None
        if not isinstance(result, dict):
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            raise LLMError(f"Claude CLI exited with {proc.returncode} and no JSON result: {detail}")

        if proc.returncode != 0 or result.get("is_error") or result.get("subtype") not in (None, "success"):
            # e.g. "Not logged in · Please run /login" or error_max_structured_output_retries
            reason = str(result.get("result") or "").strip()[:200]
            raise LLMError(f"Claude CLI error ({result.get('subtype')}, exit {proc.returncode}): {reason}")
        structured = result.get("structured_output")
        if isinstance(structured, dict):
            return structured
        # Fallback for CLI versions without --json-schema support: parse the text answer.
        try:
            parsed = json.loads(result.get("result") or "")
        except json.JSONDecodeError as exc:
            raise LLMError("Claude CLI returned no structured output") from exc
        if not isinstance(parsed, dict):
            raise LLMError("Claude CLI returned no structured output")
        return parsed
