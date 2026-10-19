"""Backend tests with the network / CLI faked out."""

import json
import os
import stat
from types import SimpleNamespace

import httpx
import pytest

from bot.ai import LLMError, get_backend
from bot.ai.anthropic_api import AnthropicBackend
from bot.ai.claude_cli import ClaudeCLIBackend
from bot.ai.ollama import OllamaBackend
from bot.extraction import EVENT_SCHEMA

ANSWER = {"name": "BBQ", "date": "2026-10-10", "time": "13:00", "location": "Park", "prices": [], "extra_info": ""}


# --- factory ----------------------------------------------------------------------------------------

def test_factory_explicit_and_auto(tmp_path):
    assert get_backend("ollama", env={}).name == "ollama"
    assert get_backend("ollama", "qwen3:8b", env={}).model == "qwen3:8b"
    assert get_backend(env={"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "x"}).name == "anthropic"
    # auto: API key wins, then the CLI, then Ollama
    assert get_backend(env={"ANTHROPIC_API_KEY": "x"}).name == "anthropic"
    fake_cli = tmp_path / "claude"
    fake_cli.write_text("#!/bin/sh\n")
    fake_cli.chmod(0o755)
    assert get_backend(env={"CLAUDE_CLI_PATH": str(fake_cli)}).name == "claude-cli"
    assert get_backend(env={"CLAUDE_CLI_PATH": "/nope/claude"}).name == "ollama"
    with pytest.raises(LLMError):
        get_backend("gpt-9", env={})


def test_factory_rejects_provider_name_as_model():
    # Regression: a stale `export LLM_MODEL=claude-cli` in the shell was passed to `claude --model`.
    with pytest.raises(LLMError, match="provider name, not a model"):
        get_backend("ollama", env={"LLM_MODEL": "claude-cli"})
    assert get_backend("ollama", env={"LLM_MODEL": "  "}).model == "qwen2.5:7b"  # blank -> default


# --- ollama -----------------------------------------------------------------------------------------

def test_ollama_sends_schema_and_parses():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(ANSWER)}})

    backend = OllamaBackend("qwen2.5:7b", transport=httpx.MockTransport(handler))
    assert backend.extract_json("sys", "user", EVENT_SCHEMA) == ANSWER
    assert captured["format"] == EVENT_SCHEMA and captured["stream"] is False
    assert captured["messages"][0] == {"role": "system", "content": "sys"}
    # Regression: thinking models (gemma4) reasoned until the timeout when `think` wasn't sent.
    assert captured["think"] is False
    assert captured["options"] == {"num_predict": 512, "temperature": 0.0}


def test_ollama_think_num_predict_temperature_from_env():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.clear()
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": json.dumps(ANSWER)}})

    from_env = get_backend("ollama", env={"OLLAMA_THINK": "high", "OLLAMA_NUM_PREDICT": "1024",
                                          "OLLAMA_TEMPERATURE": "0.7"})
    assert (from_env.think, from_env.num_predict, from_env.temperature) == ("high", 1024, 0.7)

    transport = httpx.MockTransport(handler)
    OllamaBackend(think="high", num_predict="1024", temperature="0.7", transport=transport).extract_json(
        "s", "u", EVENT_SCHEMA)
    assert captured["think"] == "high"
    assert captured["options"] == {"num_predict": 1024, "temperature": 0.7}

    # "default" leaves the choice to the model: nothing is sent
    OllamaBackend(think="default", temperature="default", transport=transport).extract_json("s", "u", EVENT_SCHEMA)
    assert "think" not in captured and "temperature" not in captured["options"]
    assert OllamaBackend(think="true").think is True
    assert OllamaBackend(think="").think is False

    with pytest.raises(LLMError, match="OLLAMA_NUM_PREDICT"):
        OllamaBackend(num_predict="lots")
    with pytest.raises(LLMError, match="OLLAMA_TEMPERATURE"):
        OllamaBackend(temperature="hot")


def test_ollama_output_limit_reached():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": '{"name": "BB', "thinking": "hmm " * 100},
                                         "done_reason": "length", "eval_count": 512})

    with pytest.raises(LLMError, match="OLLAMA_NUM_PREDICT limit .*OLLAMA_THINK=false"):
        OllamaBackend(think="true", transport=httpx.MockTransport(handler)).extract_json("s", "u", EVENT_SCHEMA)


def test_ollama_keep_alive_and_warm_up():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if not body["messages"]:  # warm-up: empty chat just loads the model
            return httpx.Response(200, json={"model": body["model"], "done": True})
        return httpx.Response(200, json={"message": {"content": json.dumps(ANSWER)}})

    backend = OllamaBackend("qwen2.5:7b", keep_alive="1h", transport=httpx.MockTransport(handler))
    assert backend.warm_up() >= 0
    backend.extract_json("s", "u", EVENT_SCHEMA)
    assert bodies[0] == {"model": "qwen2.5:7b", "messages": [], "keep_alive": "1h"}
    assert bodies[1]["keep_alive"] == "1h"
    assert OllamaBackend(keep_alive="-1").keep_alive == -1
    assert OllamaBackend().keep_alive == "30m"


def test_ollama_local_host_ignores_proxy_env():
    assert OllamaBackend(host="http://localhost:11434").trust_env is False
    assert OllamaBackend(host="http://127.0.0.1:11434").trust_env is False
    assert OllamaBackend(host="http://gpu-box.lan:11434").trust_env is True


def test_ollama_timeout_vs_unreachable_messages():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LLMError, match="didn't answer within .*LLM_TIMEOUT_SECONDS"):
        OllamaBackend(transport=httpx.MockTransport(slow)).extract_json("s", "u", EVENT_SCHEMA)
    with pytest.raises(LLMError, match="Can't reach Ollama .*ollama serve"):
        OllamaBackend(transport=httpx.MockTransport(down)).extract_json("s", "u", EVENT_SCHEMA)


def _slow_chat(loaded_models: list[str]):
    """/api/chat times out; /api/ps reports which models are in memory."""
    def handler(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in loaded_models]})
        raise httpx.ReadTimeout("timed out", request=request)
    return httpx.MockTransport(handler)


def test_ollama_timeout_tells_loading_from_slow_generation():
    with pytest.raises(LLMError, match="isn't in memory, so it was probably still loading"):
        OllamaBackend("gemma4:e4b", transport=_slow_chat([])).extract_json("s", "u", EVENT_SCHEMA)
    with pytest.raises(LLMError, match="is loaded, so generating .*OLLAMA_THINK=true\\), set OLLAMA_THINK=false"):
        OllamaBackend("gemma4:e4b", think="true", transport=_slow_chat(["gemma4:e4b"])).extract_json(
            "s", "u", EVENT_SCHEMA)
    with pytest.raises(LLMError, match="unlimited \\(OLLAMA_NUM_PREDICT=-1\\).*LLM_TIMEOUT_SECONDS"):
        OllamaBackend("llama3", num_predict="-1", transport=_slow_chat(["llama3:latest"])).extract_json(
            "s", "u", EVENT_SCHEMA)


def test_ollama_model_missing():
    backend = OllamaBackend("nope", transport=httpx.MockTransport(lambda r: httpx.Response(404, json={})))
    with pytest.raises(LLMError, match="ollama pull nope"):
        backend.extract_json("s", "u", EVENT_SCHEMA)


# --- anthropic --------------------------------------------------------------------------------------

def test_anthropic_forced_tool_call():
    calls = {}

    class Messages:
        def create(self, **kw):
            calls.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=ANSWER)])

    backend = AnthropicBackend(client=SimpleNamespace(messages=Messages()))
    assert backend.extract_json("sys", "user", EVENT_SCHEMA) == ANSWER
    assert calls["tool_choice"] == {"type": "tool", "name": "record_event"}
    assert calls["tools"][0]["input_schema"] == EVENT_SCHEMA
    assert backend.model == "claude-haiku-4-5"


# --- claude cli -------------------------------------------------------------------------------------

def _fake_cli(tmp_path, output: dict, exit_code: int = 0):
    """A stand-in `claude` that records its args/stdin and prints a canned JSON result."""
    log = tmp_path / "args.json"
    script = tmp_path / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        f"json.dump({{'args': sys.argv[1:], 'stdin': sys.stdin.read(), 'cwd': os.getcwd()}}, open({str(log)!r}, 'w'))\n"
        f"print({json.dumps(json.dumps(output))})\n"
        f"sys.exit({exit_code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


def test_claude_cli_structured_output(tmp_path):
    script, log = _fake_cli(tmp_path, {"type": "result", "subtype": "success", "is_error": False,
                                       "result": "", "structured_output": ANSWER})
    backend = ClaudeCLIBackend(cli_path=str(script))
    assert backend.extract_json("sys", "the message", EVENT_SCHEMA) == ANSWER
    rec = json.loads(log.read_text())
    args = rec["args"]
    assert rec["stdin"] == "the message"                       # prompt via stdin, not argv
    assert args[args.index("--json-schema") + 1] == json.dumps(EVENT_SCHEMA)
    assert args[args.index("--tools") + 1] == ""               # no tools
    assert "--no-session-persistence" in args and args[args.index("--model") + 1] == "haiku"
    assert rec["cwd"] != os.getcwd()                           # isolated temp dir


def test_claude_cli_errors(tmp_path):
    script, _ = _fake_cli(tmp_path, {"subtype": "error_max_structured_output_retries", "is_error": True})
    with pytest.raises(LLMError, match="error_max_structured_output_retries"):
        ClaudeCLIBackend(cli_path=str(script)).extract_json("s", "u", EVENT_SCHEMA)
    # real-world shape when the CLI isn't logged in
    script2, _ = _fake_cli(tmp_path, {"type": "result", "subtype": "success", "is_error": True,
                                      "result": "Not logged in · Please run /login"}, exit_code=1)
    with pytest.raises(LLMError, match="Not logged in"):
        ClaudeCLIBackend(cli_path=str(script2)).extract_json("s", "u", EVENT_SCHEMA)
    with pytest.raises(LLMError, match="not found"):
        ClaudeCLIBackend(cli_path="/nope/claude")
