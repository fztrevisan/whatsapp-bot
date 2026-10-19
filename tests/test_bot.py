import hashlib
import hmac
import json
from datetime import date, datetime, time, timezone

from fastapi.testclient import TestClient

from bot.ai import LLMError
from bot.app import create_app
from bot.commands import (AI_ERROR_REPLY, SAVED_REPLY, USAGE_REPLY, Command, EventStore, execute,
                          format_event_list, parse_command)
from bot.config import Settings
from bot.extraction import EventDetails, extract_event, normalize

GROUP = "120363000000000000@g.us"
SECRET = "test-secret-0123456789"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
SETTINGS = Settings(
    openwa_url="http://openwa", api_key="k", session_id="sess-1",
    group_id=GROUP, webhook_secret=SECRET, reply_to_self=False, timezone="UTC",
)

FULL = {"name": "Team *BBQ*", "date": "2026-10-10", "time": "13:00", "location": "Parque Ibirapuera",
        "prices": ["Entry: R$ 40", ""], "extra_info": "Bring drinks"}
NO_LOC = {**FULL, "location": ""}
NO_BOTH = {**FULL, "location": "", "date": "next week-ish"}


def fake_extractor(responses):
    """Returns canned model outputs in order, run through the real normalize()."""
    it = iter(responses)
    return lambda text: normalize(next(it))


# --- parsing --------------------------------------------------------------------------------------

def test_parse_command():
    assert parse_command("list-events") == Command("list")
    assert parse_command("  LIST-EVENTS ") == Command("list")
    assert parse_command("schedule-event BBQ sat\nat the park") == Command("schedule", "BBQ sat\nat the park")
    assert parse_command("schedule-event") == Command("schedule", "")
    for body in ["hello", "please list-events", "schedule-events foo", "list-events now", "", None]:
        assert parse_command(body) is None


# --- normalize / extraction -----------------------------------------------------------------------

def test_normalize_cleans_and_validates():
    d = normalize(FULL)
    assert d.name == "Team BBQ"                         # formatting chars stripped
    assert d.date == date(2026, 10, 10) and d.time == time(13, 0)
    assert d.prices == ["Entry: R$ 40"]                 # empty entries dropped
    assert d.missing == []
    bad = normalize({"name": "", "date": "tomorrow", "time": "7pm", "location": "  ", "prices": "R$ 10"})
    assert bad.name == "Untitled event" and bad.date is None and bad.time is None
    assert bad.prices == ["R$ 10"]
    assert bad.missing == ["date", "location"]


def test_extract_event_passes_reference_date_and_schema():
    seen = {}

    class Backend:
        name, model = "fake", "fake-1"

        def extract_json(self, system, user, schema):
            seen.update(system=system, user=user, schema=schema)
            return FULL

    d = extract_event("BBQ saturday", Backend(), NOW)
    assert d.location == "Parque Ibirapuera"
    assert "2026-10-06" in seen["user"] and "<message>\nBBQ saturday\n</message>" in seen["user"]
    assert set(seen["schema"]["required"]) == {"name", "date", "time", "location", "prices", "extra_info"}


# --- execute ----------------------------------------------------------------------------------------

def test_schedule_saved_with_summary_then_listed():
    store = EventStore()
    reply = execute(Command("schedule", "bbq"), "Ana", store, fake_extractor([FULL]), NOW)
    assert reply.startswith(SAVED_REPLY)
    assert "*Team BBQ*" in reply and "_Date:_ Sat, Oct 10, 2026 · 13:00" in reply
    assert "_Location:_ Parque Ibirapuera" in reply and "_Prices:_ Entry: R$ 40" in reply
    assert "_Info:_ Bring drinks" in reply
    listing = execute(Command("list"), "Bo", store, fake_extractor([]), NOW)
    assert listing.startswith("*OK, here are your scheduled events:*")
    assert "1. *Team BBQ*" in listing and "added by Ana" in listing and "Total: *1* event" in listing


def test_missing_fields_not_saved():
    store = EventStore()
    r1 = execute(Command("schedule", "x"), "A", store, fake_extractor([NO_LOC]), NOW)
    assert "couldn't find the *location*" in r1 and "*not* saved" in r1
    r2 = execute(Command("schedule", "x"), "A", store, fake_extractor([NO_BOTH]), NOW)
    assert "*date* and *location*" in r2
    assert store.all() == []


def test_usage_and_ai_error():
    store = EventStore()
    assert execute(Command("schedule", ""), "A", store, fake_extractor([]), NOW) == USAGE_REPLY

    def boom(text):
        raise LLMError("model down")

    assert execute(Command("schedule", "x"), "A", store, boom, NOW) == AI_ERROR_REPLY
    assert store.all() == []


def test_list_sorted_by_date():
    store = EventStore()
    later = normalize({**FULL, "name": "Later", "date": "2026-12-01"})
    sooner = normalize({**FULL, "name": "Sooner", "date": "2026-10-07", "time": ""})
    store.add(later, "A", NOW)
    store.add(sooner, "B", NOW)
    out = format_event_list(store.all())
    assert out.index("1. *Sooner*") < out.index("2. *Later*")
    assert "Wed, Oct 07, 2026\n" in out  # no time -> date only
    assert "No events yet" in format_event_list([])


# --- webhook ----------------------------------------------------------------------------------------

def _post(client, data, key="k1", secret=SECRET, event="message.received"):
    body = json.dumps({"event": event, "sessionId": "sess-1", "data": data}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers={
        "Content-Type": "application/json", "X-OpenWA-Signature": sig, "X-OpenWA-Idempotency-Key": key,
    })


def _client(responses=()):
    sent = []
    app = create_app(SETTINGS, EventStore(), send=lambda c, t, q: sent.append((c, t, q)),
                     extract=fake_extractor(list(responses)))
    return TestClient(app), sent


def _msg(body, **kw):
    return {"id": "m1", "chatId": GROUP, "from": GROUP, "body": body, "type": "text",
            "fromMe": False, "isGroup": True, "contact": {"pushName": "Ana_"}, **kw}


def test_webhook_schedule_and_list():
    client, sent = _client([FULL])
    assert _post(client, _msg("schedule-event BBQ sat 1pm park"), key="a").json()["status"] == "accepted"
    assert _post(client, _msg("list-events", id="m2"), key="b").json()["status"] == "accepted"
    assert sent[0][0] == GROUP and sent[0][2] == "m1" and sent[0][1].startswith(SAVED_REPLY)
    assert "1. *Team BBQ*" in sent[1][1] and "_added by Ana_" in sent[1][1]  # "_" stripped from name


def test_webhook_rejects_bad_signature():
    client, sent = _client()
    assert _post(client, _msg("list-events"), secret="wrong-secret-000000").status_code == 401
    assert sent == []


def test_webhook_filters():
    client, sent = _client()
    assert _post(client, _msg("list-events", chatId="other@g.us"), key="1").json()["status"] == "ignored"
    assert _post(client, _msg("list-events", fromMe=True), key="2").json()["status"] == "ignored"
    assert _post(client, _msg("list-events", type="image"), key="3").json()["status"] == "ignored"
    assert _post(client, _msg("list-events"), key="4", event="message.ack").json()["status"] == "ignored"
    assert _post(client, _msg("just chatting"), key="5").json()["status"] == "no-match"
    assert sent == []


def test_webhook_dedupes_redelivery():
    client, sent = _client([FULL, FULL])
    assert _post(client, _msg("schedule-event Once"), key="same").json()["status"] == "accepted"
    assert _post(client, _msg("schedule-event Once"), key="same").json()["status"] == "duplicate"
    assert len(sent) == 1
    assert len(client.app.state.store.all()) == 1
