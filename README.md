# OpenWA keyword bot PoC

A small Python bot that watches **one WhatsApp group** and answers two commands:

| You type in the group | Bot replies |
| --- | --- |
| `schedule-event <text>` | An AI model pulls the event's name, date, time, location, prices and extra info out of the text. If it finds a **date and location**, the bot saves the event and replies "Cool, your event has been saved. Type `list-events` to check all existing events.", followed by a summary of what was extracted. If either is missing, nothing is saved and the bot asks for it. |
| `list-events` | **OK, here are your scheduled events:** then a numbered list sorted by date, with every detail and who added it. |
|  |  |

- **Events** live in an in-memory Python list. Restarting the bot clears them.
- **Messages** are never logged or saved by the bot. Each one is read when it arrives, matched against the commands, and dropped.
- **AI provider** is plug and play: Claude (API key or your local Claude Code login) or any local Ollama model, such as Qwen. You switch with one variable. See [AI providers](#ai-providers).

## Table of Contents
- [How it works](#how-it-works)
- [1. Which phone number to use](#1-which-phone-number-to-use)
- [2. Prerequisites](#2-prerequisites)
- [3. Run the OpenWA gateway](#3-run-the-openwa-gateway)
- [4. Set up the Python bot](#4-set-up-the-python-bot)
- [5. Link WhatsApp to OpenWA (QR scan)](#5-link-whatsapp-to-openwa-qr-scan)
- [6. Run the bot and connect the webhook](#6-run-the-bot-and-connect-the-webhook)
- [7. Try it](#7-try-it)
- [AI providers](#ai-providers)
- [Configuration reference](#configuration-reference)
  - [Bot (`.env`)](#bot-env)
  - [OpenWA (`OpenWA/.env`)](#openwa-openwaenv)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)

## How it works

```
 WhatsApp group ──► OpenWA gateway (Docker, :2785) ──webhook (HMAC-signed)──► Python bot (FastAPI, :8000)
                         ▲                                                          │
                         └──────────── POST /messages/send-text ◄───────────────────┘
```

**OpenWA's role.** [OpenWA](https://github.com/rmyndharis/OpenWA) logs into WhatsApp as a *linked device*, the same way WhatsApp Web does, on a phone number you control.

> [!IMPORTANT]
> OpenWA is an unnoficial gateway and using it might have consequences, such as account restriction or ban.
> Please read the [maintainer's disclaimer](https://github.com/rmyndharis/OpenWA#%EF%B8%8F-before-you-connect-a-number--please-read) before using it.
> This repo is meant to be a PoC only. Do **not** use this in production.

**The webhook.** For each new message in the target group, OpenWA calls the bot's webhook. A webhook filter makes sure only that group's messages are sent.

**The bot.** It checks the HMAC signature and runs a cheap regex to see if the message is a command. Normal chat stops there. For `schedule-event`, the text after the keyword goes to the AI provider, which returns structured JSON. The bot validates it and replies through OpenWA's REST API. The AI call runs in the background, after OpenWA has already gotten its `200`, so a slow model never times out the webhook.

```
schedule-event <text> ──► regex trigger ──► AI backend (Claude API | Claude CLI | Ollama)
                                                  │  JSON: name, date, time, location, prices, extra_info
                                                  ▼
                          date + location present? ── yes ──► save to list, reply with summary
                                                  └──── no ──► reply asking for the missing field(s)
```

The [OpenWA-plugins](https://github.com/rmyndharis/OpenWA-plugins) repo isn't used. Plugins are Node.js only. This PoC is Python, so it talks to OpenWA over HTTP instead.

---

## 1. Which phone number to use

**Use a dedicated, disposable number. Never your personal or work number.**

OpenWA is an unofficial client, and WhatsApp can restrict or ban numbers it flags as automated.

- **Get a second number.** A cheap prepaid SIM, or a second line/eSIM, works. Install **WhatsApp** or **WhatsApp Business** for it on a phone, and keep that phone online and charged. Your bot runs as a *linked device* of that phone.
- **Warm the number up.** For a few days before the test, use it like a human would: add a profile photo, chat with a couple of contacts, join a group.
- **Add the bot number to the test group,** like any other member. Then test from **your own phone**: send commands as a normal member of the group. By default the bot ignores messages sent by its own number (`REPLY_TO_SELF=false`).
- **Keep it small.** Use a test group of people who know about the bot, and keep message volume low.

## 2. Prerequisites

- macOS with **Docker Desktop**, given at least 2–4 GB of RAM. The default engine runs headless Chromium.
- **Python 3.13+**
- **uv** installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **git**

## 3. Run the OpenWA gateway

Build it from source **at the commit that was reviewed**. Don't pull the `:latest` image.

```bash
cd ~/workspaces/personal/openwa
git clone https://github.com/rmyndharis/OpenWA.git
cd OpenWA
git checkout 022f4bc97fca9139b0b14bf3eeae1fc0fac3a91a
```

Create `OpenWA/.env`. Docker Compose reads it automatically.

```bash
cat > .env <<EOF
# Admin key, used only on first boot. Keep it secret.
API_MASTER_KEY=$(python3 -c "import secrets; print('owa_' + secrets.token_hex(24))")
# Let OpenWA (inside Docker) call the bot running on your Mac.
SSRF_ALLOWED_HOSTS=host.docker.internal
# Keep OpenWA's own copy of messages as briefly as possible (1 day is the minimum).
MESSAGE_RETENTION_DAYS=1
# Don't phone home to GitHub for update checks.
UPDATE_CHECK_ENABLED=false
# Lower ban risk than baileys (it drives a real Chromium).
ENGINE_TYPE=whatsapp-web.js
# Optional hardening, see SECURITY_ASSESSMENT.md risk 2. Uncomment if QR/login still works for you.
# WWEBJS_WEB_VERSION=off
EOF
grep API_MASTER_KEY .env   # copy this value: it's your OPENWA_API_KEY
```

Start it. The first build takes a few minutes.

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker logs -f openwa-api        # wait for the startup banner, then Ctrl+C
```

- **Dashboard:** http://localhost:2785, log in with the API key
- **Swagger:** http://localhost:2785/api/docs
- The API listens on `127.0.0.1` only.

> On **Linux** (not Docker Desktop), `host.docker.internal` doesn't exist by default. Add
> `extra_hosts: ["host.docker.internal:host-gateway"]` to the `openwa` service in `docker-compose.dev.yml`.

## 4. Set up the Python bot

```bash
cd ~/workspaces/personal/openwa
uv sync
cp .env.example .env
```

Fill in `.env`:

- `OPENWA_API_KEY`: the `API_MASTER_KEY` from step 3
- `WEBHOOK_SECRET`: generate one with `python -c "import secrets; print(secrets.token_hex(24))"`

Leave the other values for now. The next step fills them in.

Then choose an AI provider. On this Mac, the default `LLM_PROVIDER=auto` picks up your Claude Code login if you have no API key, so it usually needs no changes. Before touching WhatsApp, check that extraction works:

```bash
uv run try-extract "Churrasco do time sábado 13h no Parque Ibirapuera, R\$ 40 por pessoa, levar bebida"
```

It prints the provider, the raw JSON from the model, and the reply the bot would send. See [AI providers](#ai-providers) for the other options.

## 5. Link WhatsApp to OpenWA (QR scan)

```bash
# 1) Create a session. Copy the printed OPENWA_SESSION_ID into .env.
uv run setup-openwa create-session poc-bot

# 2) Start it and get the QR code (saved as qr.png).
uv run setup-openwa qr && open qr.png
```

Scan the QR from the **bot's phone**:

1. Open WhatsApp.
2. Go to **Settings → Linked devices → Link a device**.
3. Scan `qr.png`.

The QR rotates every ~20 s. If it expires, run `qr` again, or use the live QR in the dashboard (**Sessions** page).

```bash
# 3) Check that the session is linked: status should be "ready".
uv run setup-openwa status

# 4) Find the group's id. Copy it into .env as WHATSAPP_GROUP_ID (format: 1203630...@g.us).
uv run setup-openwa groups
```

Linking survives restarts: the login is kept in `OpenWA/data/sessions`. To unlink, remove the device from the phone's **Linked devices** screen.

## 6. Run the bot and connect the webhook

```bash
# Terminal 1: the bot
uv run uvicorn bot.app:get_app --factory --host 0.0.0.0 --port 8000

# Terminal 2: tell OpenWA to send this group's messages to the bot
uv run setup-openwa webhook
```

The `webhook` command registers `BOT_WEBHOOK_URL` (default `http://host.docker.internal:8000/webhook`) with:

- `events: ["message.received"]`
- your `WEBHOOK_SECRET` for HMAC signing
- a filter so only `WHATSAPP_GROUP_ID` is forwarded

Running it again replaces the old registration.

The bot listens on `0.0.0.0` so the Docker VM can reach it. If you're on an untrusted network, keep the macOS firewall on.

## 7. Try it

In the group, from your own phone:

```
schedule-event Churrasco do time sábado 13h no Parque Ibirapuera, R$ 40 por pessoa, levar bebida
schedule-event Board games night next Friday 8pm at Ana's place
schedule-event Sprint retro sometime next week
list-events
```

The first two get saved. The third is rejected because it has no date or location. A saved reply looks like:

```
Cool, your event has been saved. Type `list-events` to check all existing events.

*Churrasco do time*
_Date:_ Sat, Oct 10, 2026 · 13:00
_Location:_ Parque Ibirapuera
_Prices:_ R$ 40 por pessoa
_Info:_ levar bebida
```

The bot replies to each command as a quoted reply. Commands are case-insensitive, and the event text can span several lines. Relative dates like "sábado" or "next Friday" are resolved using `EVENT_TIMEZONE`. `schedule-event` with no text gets a usage hint. Any other message is ignored.

`GET http://localhost:8000/health` shows whether the bot is up, how many events it holds, and which AI provider and model it's using.

## AI providers

The bot talks to the model through one small interface (`bot/ai/base.py`): *system prompt + text + JSON Schema in, JSON out*. Each provider is a ~50-line class, and `LLM_PROVIDER` picks one at startup.

| `LLM_PROVIDER` | Uses | Setup | Default model | Where the text goes |
| --- | --- | --- | --- | --- |
| `claude-cli` | Your local **Claude Code** login (subscription). No API key. | `claude` installed and logged in (`claude auth status`) | `haiku` | Anthropic |
| `anthropic` | **Anthropic API** | `ANTHROPIC_API_KEY` from console.anthropic.com (billed separately from Claude Desktop) | `claude-haiku-4-5` | Anthropic |
| `ollama` | Any local model in **Ollama** | `ollama pull qwen2.5:7b`, Ollama running | `qwen2.5:7b` | Stays on your machine |
| `auto` (default) | API key if set, else Claude CLI if installed, else Ollama | | | |

Switching is just `.env` changes, then restarting the bot:

```bash
# This Mac: Claude via Claude Code login
LLM_PROVIDER=claude-cli

# Another PC: local Qwen through Ollama
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b            # or qwen3:8b, llama3.1:8b, gemma3:12b ...
OLLAMA_HOST=http://localhost:11434
```

You can also try a provider without changing `.env`:

```bash
uv run try-extract --provider ollama --model qwen3:8b "Board games tomorrow 8pm at Ana's"
```

**How each one returns reliable JSON:**

- **Anthropic API:** a forced tool call, with the event schema as the tool's input.
- **Claude CLI:** `claude -p --json-schema …`. The CLI validates the output against the schema and retries if it doesn't match. It runs with no tools, no MCP servers, from an empty temp folder, and with `--no-session-persistence`, so no transcript of the message is written to `~/.claude`.
- **Ollama:** the `format` field set to the JSON Schema. Ollama constrains the model's output to it, which works well even for 7B models.

Whatever the model returns, the bot validates it again. A date that isn't a real `YYYY-MM-DD` counts as missing, and formatting characters are stripped.

**Speed:**

- **Anthropic API:** ~1–2 s.
- **Claude CLI:** ~4–10 s, since it starts a process for each event.
- **Ollama:** depends on the hardware. A 7B model on Apple Silicon takes a few seconds once loaded. Loading it can take much longer, especially on CPU-only PCs. To avoid that load delay, the bot:
- loads the model in the background when it starts, and logs `AI model '…' ready (loaded in Ns)`
- asks Ollama to keep it in memory for `OLLAMA_KEEP_ALIVE` (default `30m`) after each request

  Wait for the "ready" log line before testing. Each event also logs `AI extraction took Ns`, so you can size `LLM_TIMEOUT_SECONDS`.

**Privacy:** only the text after `schedule-event` is sent to the AI. Normal chat messages never are. With the Claude providers, that text goes to Anthropic. With Ollama it never leaves the machine.

**Why not LangChain?** It would cover the Anthropic API and Ollama, but not the Claude Code CLI login you want to use here. It would also add a large dependency tree for a single structured-output call. The interface here is small enough that adding a provider (OpenAI, LM Studio, vLLM…) means writing one class with `name`, `model` and `extract_json()`, then registering it in `bot/ai/__init__.py`.

## Configuration reference

### Bot (`.env`)

| Variable | Required | Description |
| --- | --- | --- |
| `OPENWA_URL` | no | Gateway URL. Default `http://localhost:2785`. |
| `OPENWA_API_KEY` | yes | OpenWA API key, sent as `X-API-Key`. |
| `OPENWA_SESSION_ID` | yes | Session UUID from `create-session`. |
| `WHATSAPP_GROUP_ID` | yes | Target group JID (`…@g.us`). Messages from any other chat are ignored. |
| `WEBHOOK_SECRET` | yes | HMAC secret, at least 16 chars. Must match the one in the webhook registration. |
| `BOT_WEBHOOK_URL` | no | URL OpenWA uses to reach the bot. Default `http://host.docker.internal:8000/webhook`. |
| `REPLY_TO_SELF` | no | `true` makes the bot answer commands sent from its own number. Default `false`. |
| `LLM_PROVIDER` | no | `auto` (default), `claude-cli`, `anthropic` or `ollama`. |
| `LLM_MODEL` | no | Model override. Defaults: `haiku` (CLI), `claude-haiku-4-5` (API), `qwen2.5:7b` (Ollama). |
| `LLM_TIMEOUT_SECONDS` | no | Give up on an AI call after this long. Default `120`. |
| `ANTHROPIC_API_KEY` | for `anthropic` | Anthropic API key. |
| `CLAUDE_CLI_PATH` | no | Path to the `claude` binary. Default `claude`. |
| `CLAUDE_CLI_EXTRA_ARGS` | no | Extra CLI flags, e.g. `--safe-mode` to skip your personal hooks, plugins and CLAUDE.md. |
| `OLLAMA_HOST` | no | Ollama URL. Default `http://localhost:11434`. |
| `OLLAMA_KEEP_ALIVE` | no | How long the model stays loaded after each request: `30m` (default), `2h`, or `-1` for forever. |
| `OLLAMA_THINK` | no | Reasoning pass for thinking models (qwen3, gemma4…): `false` (default), `true`, a level like `high`, or `default` for the model's own setting. Leaving it on makes extraction slow and can run until the timeout. |
| `OLLAMA_NUM_PREDICT` | no | Max tokens the model may generate, thinking included. Default `512` (the event JSON is ~150). `-1` = no limit. |
| `OLLAMA_TEMPERATURE` | no | Sampling temperature. Default `0` (deterministic). `default` uses the model's Modelfile value. |
| `EVENT_TIMEZONE` | no | IANA timezone for resolving "tomorrow" or "sábado", e.g. `America/Sao_Paulo`. Empty means this computer's timezone. |

### OpenWA (`OpenWA/.env`)

| Variable | Value | Why |
| --- | --- | --- |
| `API_MASTER_KEY` | random | Admin key seeded on first boot. |
| `SSRF_ALLOWED_HOSTS` | `host.docker.internal` | OpenWA blocks webhooks to private addresses unless the host is allow-listed. |
| `MESSAGE_RETENTION_DAYS` | `1` | OpenWA stores messages in its own DB. This prunes them daily. |
| `UPDATE_CHECK_ENABLED` | `false` | No calls to GitHub. |
| `ENGINE_TYPE` | `whatsapp-web.js` | Lower ban risk than `baileys`. |

## Project layout

```
bot/commands.py          regex triggers, in-memory event list, WhatsApp formatting
bot/extraction.py        event JSON Schema, prompt, validation of the model's answer
bot/ai/                  pluggable AI providers
  base.py                  the interface (extract_json)
  claude_cli.py            Claude via `claude -p` (Claude Code login)
  anthropic_api.py         Claude via API key
  ollama.py                local models via Ollama
  __init__.py              get_backend(): picks the provider from LLM_PROVIDER
bot/app.py               FastAPI webhook: signature check, filters, de-dup, background processing
bot/openwa_client.py     tiny OpenWA REST client (send-text + setup calls)
bot/config.py            settings from .env
scripts/setup_openwa.py  create session, QR, list groups, register webhook
scripts/try_extract.py   test the AI extraction from the terminal
tests/                   pytest suite (AI providers are faked, no network needed)
```

Run the tests with `uv run pytest -q`.

## Troubleshooting

- **The `webhook` command fails with `400`, mentioning SSRF.** `SSRF_ALLOWED_HOSTS=host.docker.internal` is missing from `OpenWA/.env`. Add it, then restart: `docker compose -f docker-compose.dev.yml up -d`.
- **No reply in the group.** Work through these:
- Is the session `ready`?
- Did the bot terminal log a `POST /webhook`?
- A `401` there means the secrets don't match. Re-run `uv run setup-openwa webhook` after changing `WEBHOOK_SECRET`.
- Are you sending from the bot's own number? Those messages are ignored by default.
- Undelivered webhooks are listed under `GET /api/webhooks/delivery-failures`.
- **Bot replies "Sorry, I couldn't process that event right now".** The AI call failed, and the bot terminal logs why. Common causes:
- `Not logged in`: run `claude` once and log in. Check with `claude auth status`.
- `ollama pull <model>`: the model isn't downloaded yet.
- `Can't reach Ollama`: Ollama isn't running, or `OLLAMA_HOST` is wrong.
- `Ollama didn't answer within Ns`: Ollama was reached, but the model was still loading or is too slow for this PC.
  - Wait for the bot's "model ready" log line.
  - Raise `LLM_TIMEOUT_SECONDS`.
  - Use a smaller model.
  - Set `OLLAMA_KEEP_ALIVE=-1` so the model is never unloaded.
  - The error says whether the model was in memory. If it was, generation itself was slow: check `OLLAMA_THINK=false`. Each successful call logs `Ollama stats: …` with load time, token counts and thinking length.
- A timeout with the Claude CLI: raise `LLM_TIMEOUT_SECONDS`.

  Reproduce it with `uv run try-extract "..."`.
- **A setting isn't what you expect.** `.env` always wins over variables exported in your shell. When it replaces one, the bot prints `[config] .env overrides these variables exported in your shell: …`; run the `unset …` it suggests. A variable that isn't in `.env` at all is still read from the shell. `try_extract` and the bot's startup log show where the AI settings came from: `.env`, `shell`, `unset` or a command-line flag.
- **Wrong dates, like "saturday" landing on the wrong week.** Set `EVENT_TIMEZONE`. If a small local model keeps misreading dates, try a bigger one (`qwen2.5:14b`) or Claude.
- **No QR / session stuck.** Look at `docker logs openwa-api`. Chromium needs memory: raise Docker Desktop's RAM, or set `OPENWA_MEM_LIMIT=3g` in `OpenWA/.env`.
- **The number got restricted.** OpenWA can't undo that. Appeal through WhatsApp and use another number.

## Cleanup

1. On the bot's phone, go to **Linked devices** and log the device out.
2. Stop OpenWA: `cd OpenWA && docker compose -f docker-compose.dev.yml down`
3. Delete `OpenWA/data/`. It holds the WhatsApp login and OpenWA's stored messages.