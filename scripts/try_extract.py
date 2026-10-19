"""Test the AI extraction without WhatsApp. Run from the project root:

    python -m scripts.try_extract "Churrasco do time sábado 13h no Parque Ibirapuera, R$ 40 por pessoa"
    python -m scripts.try_extract --provider ollama --model qwen2.5:7b "Board games night tomorrow 8pm at Ana's place"

Prints the raw JSON from the model and the reply the bot would send.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from bot.ai import PROVIDERS, LLMError, get_backend
from bot.commands import format_missing, format_saved
from bot.config import Settings, env_source
from bot.extraction import EVENT_SCHEMA, SYSTEM_PROMPT, build_user_prompt, normalize


def main() -> None:
    parser = argparse.ArgumentParser(description="Try the event extractor")
    parser.add_argument("text", help="the text you'd type after schedule-event")
    parser.add_argument("--provider", choices=PROVIDERS, help="overrides LLM_PROVIDER")
    parser.add_argument("--model", help="overrides LLM_MODEL")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # shows the Ollama stats line
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings.from_env()  # also loads .env
    try:
        print(f"LLM_PROVIDER from: {'--provider flag' if args.provider else env_source('LLM_PROVIDER')}  |  "
              f"LLM_MODEL from: {'--model flag' if args.model else env_source('LLM_MODEL')}")
        backend = get_backend(args.provider, args.model)
        print(f"Provider: {backend.name}  model: {backend.model}\n")
        started = time.monotonic()
        raw = backend.extract_json(SYSTEM_PROMPT, build_user_prompt(args.text, settings.now()), EVENT_SCHEMA)
    except LLMError as exc:
        sys.exit(f"Error: {exc}")
    print(f"Raw model output ({time.monotonic() - started:.1f}s):\n{json.dumps(raw, indent=2, ensure_ascii=False)}\n")
    details = normalize(raw)
    print("Bot reply:\n" + (format_missing(details) if details.missing else format_saved(details)))


if __name__ == "__main__":
    main()
