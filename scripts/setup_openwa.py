"""One-time setup helper for the PoC. Run from the project root:

    python -m scripts.setup_openwa create-session poc-bot   # prints the session id
    python -m scripts.setup_openwa qr                       # starts the session, saves qr.png
    python -m scripts.setup_openwa status
    python -m scripts.setup_openwa groups                   # find your WHATSAPP_GROUP_ID
    python -m scripts.setup_openwa webhook                  # registers the bot's webhook in OpenWA

Values come from .env (OPENWA_URL, OPENWA_API_KEY, OPENWA_SESSION_ID, WHATSAPP_GROUP_ID,
WEBHOOK_SECRET, BOT_WEBHOOK_URL).
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time

import httpx

from bot.config import Settings
from bot.openwa_client import OpenWAClient


def _need(value: str, name: str) -> str:
    if not value:
        sys.exit(f"Set {name} in your .env first.")
    return value


def cmd_create_session(client: OpenWAClient, s: Settings, args: argparse.Namespace) -> None:
    session = client.create_session(args.name)
    print(f"Session created. Add this to .env:\n\nOPENWA_SESSION_ID={session['id']}")


def cmd_qr(client: OpenWAClient, s: Settings, args: argparse.Namespace) -> None:
    sid = _need(s.session_id, "OPENWA_SESSION_ID")
    try:
        client.start_session(sid)
    except httpx.HTTPStatusError as exc:
        print(f"start returned {exc.response.status_code} (fine if it's already running)")
    print("Waiting for the QR code (whatsapp-web.js can take 20-60s on first boot)...")
    for _ in range(60):
        status = client.get_session(sid).get("status")
        if status == "ready":
            print("Session is already linked and ready.")
            return
        try:
            qr = client.get_qr(sid)
        except httpx.HTTPStatusError:
            time.sleep(3)
            continue
        data_url: str = qr.get("qrCode", "")
        if data_url.startswith("data:image/png;base64,"):
            with open(args.out, "wb") as fh:
                fh.write(base64.b64decode(data_url.split(",", 1)[1]))
            print(
                f"QR saved to {args.out}. On the BOT phone: WhatsApp > Settings > Linked devices > "
                "Link a device, then scan it.\nThe QR rotates about every 20s; run this again if it expires.\n"
                "Tip: the OpenWA dashboard (http://localhost:2785) also shows a live QR."
            )
            return
        time.sleep(3)
    sys.exit("No QR received. Check `docker logs openwa-api`.")


def cmd_status(client: OpenWAClient, s: Settings, args: argparse.Namespace) -> None:
    sid = _need(s.session_id, "OPENWA_SESSION_ID")
    sess = client.get_session(sid)
    print(f"{sess['name']}: {sess['status']}  phone={sess.get('phone')}  name={sess.get('pushName')}")


def cmd_groups(client: OpenWAClient, s: Settings, args: argparse.Namespace) -> None:
    sid = _need(s.session_id, "OPENWA_SESSION_ID")
    groups = client.list_groups(sid)
    if not groups:
        print("No groups found. Add the bot's number to a group first.")
    for g in groups:
        print(f"{g['id']:<40} {g.get('name')}")


def cmd_webhook(client: OpenWAClient, s: Settings, args: argparse.Namespace) -> None:
    sid = _need(s.session_id, "OPENWA_SESSION_ID")
    group = _need(s.group_id, "WHATSAPP_GROUP_ID")
    secret = _need(s.webhook_secret, "WEBHOOK_SECRET")
    url = args.url or os.getenv("BOT_WEBHOOK_URL", "http://host.docker.internal:8000/webhook")
    for wh in client.list_webhooks(sid):
        if wh.get("url") == url:
            client.delete_webhook(sid, wh["id"])
            print(f"Removed previous webhook {wh['id']} for {url}")
    wh = client.create_webhook(sid, url, secret, group)
    print(f"Webhook {wh['id']} registered: {url} (message.received, group {group} only)")


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenWA PoC setup helper")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-session")
    p.add_argument("name", help="3-50 chars: letters, digits, hyphens")
    p = sub.add_parser("qr")
    p.add_argument("--out", default="qr.png")
    sub.add_parser("status")
    sub.add_parser("groups")
    p = sub.add_parser("webhook")
    p.add_argument("--url", help="defaults to BOT_WEBHOOK_URL or http://host.docker.internal:8000/webhook")
    args = parser.parse_args()

    s = Settings.from_env()
    client = OpenWAClient(s.openwa_url, _need(s.api_key, "OPENWA_API_KEY"))
    handlers = {
        "create-session": cmd_create_session,
        "qr": cmd_qr,
        "status": cmd_status,
        "groups": cmd_groups,
        "webhook": cmd_webhook,
    }
    try:
        handlers[args.cmd](client, s, args)
    except httpx.HTTPStatusError as exc:
        sys.exit(f"OpenWA returned {exc.response.status_code}: {exc.response.text}")
    except httpx.ConnectError:
        sys.exit(f"Can't reach OpenWA at {s.openwa_url}. Is the container running?")
    finally:
        client.close()


if __name__ == "__main__":
    main()
