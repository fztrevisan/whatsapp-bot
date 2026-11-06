import hashlib
import hmac
import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from bot.ai import LLMError
from bot.app import create_app
from bot.commands import (AI_ERROR_REPLY, SAVED_REPLY, USAGE_REPLY, Command, add_event, execute,
                          format_event_list, list_events, parse_command)
from bot.config import Settings
from bot.extraction import EventDetails, extract_event, normalize
from bot.models import Event

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


def _add(session, author="A", **over):
    return add_event(session, normalize({**FULL, **over}), author)


def _count(session):
    return session.scalar(select(func.count()).select_from(Event))


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

def test_schedule_saved_with_summary_then_listed(session):
    reply = execute(Command("schedule", "bbq"), "Ana", session, fake_extractor([FULL]), NOW)
    assert reply.startswith(SAVED_REPLY)
    assert "*Team BBQ*" in reply and "_Date:_ Sat, Oct 10, 2026 · 13:00" in reply
    assert "_Location:_ Parque Ibirapuera" in reply and "_Prices:_ Entry: R$ 40" in reply
    assert "_Info:_ Bring drinks" in reply
    listing = execute(Command("list"), "Bo", session, fake_extractor([]), NOW)
    assert listing.startswith("*OK, here are your scheduled events:*")
    assert "1. *Team BBQ*" in listing and "added by Ana" in listing and "Total: *1* event" in listing


def test_missing_fields_not_saved(session):
    r1 = execute(Command("schedule", "x"), "A", session, fake_extractor([NO_LOC]), NOW)
    assert "couldn't find the *location*" in r1 and "*not* saved" in r1
    r2 = execute(Command("schedule", "x"), "A", session, fake_extractor([NO_BOTH]), NOW)
    assert "*date* and *location*" in r2
    assert _count(session) == 0


def test_usage_and_ai_error(session):
    assert execute(Command("schedule", ""), "A", session, fake_extractor([]), NOW) == USAGE_REPLY

    def boom(text):
        raise LLMError("model down")

    assert execute(Command("schedule", "x"), "A", session, boom, NOW) == AI_ERROR_REPLY
    assert _count(session) == 0


def test_list_sorted_by_date(session):
    _add(session, name="Later", date="2026-12-01")
    _add(session, author="B", name="Sooner", date="2026-10-07", time="")
    out = format_event_list(list_events(session, NOW.date()))
    assert out.index("1. *Sooner*") < out.index("2. *Later*")
    assert "Wed, Oct 07, 2026\n" in out  # no time -> date only
    assert "No upcoming events" in format_event_list([])


def test_list_hides_past_events(session):
    for name, day in [("Yesterday", "2026-10-05"), ("Today", "2026-10-06"), ("Tomorrow", "2026-10-07")]:
        _add(session, name=name, date=day)
    out = execute(Command("list"), "A", session, fake_extractor([]), NOW)
    assert "Yesterday" not in out
    assert out.index("1. *Today*") < out.index("2. *Tomorrow*")
    assert "Total: *2* events" in out
    assert _count(session) == 3  # past events stay in the database


def test_list_uses_event_timezone_for_today(session):
    late_in_sp = datetime(2026, 10, 6, 23, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))  # already Oct 7 in UTC
    _add(session, name="Tonight", date="2026-10-06")
    _add(session, name="Old", date="2026-10-05")
    out = execute(Command("list"), "A", session, fake_extractor([]), late_in_sp)
    assert "*Tonight*" in out and "Old" not in out


def test_only_past_events_shows_empty_message(session):
    _add(session, date="2026-10-01")
    out = execute(Command("list"), "A", session, fake_extractor([]), NOW)
    assert "No upcoming events" in out
    assert _count(session) == 1


# --- webhook ----------------------------------------------------------------------------------------

def _post(client, data, key="k1", secret=SECRET, event="message.received"):
    body = json.dumps({"event": event, "sessionId": "sess-1", "data": data}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers={
        "Content-Type": "application/json", "X-OpenWA-Signature": sig, "X-OpenWA-Idempotency-Key": key,
    })


def _client(session_factory, responses=()):
    sent = []
    app = create_app(SETTINGS, session_factory, send=lambda c, t, q: sent.append((c, t, q)),
                     extract=fake_extractor(list(responses)))
    return TestClient(app), sent


def _msg(body, **kw):
    return {"id": "m1", "chatId": GROUP, "from": GROUP, "body": body, "type": "text",
            "fromMe": False, "isGroup": True, "contact": {"pushName": "Ana_"}, **kw}


def test_webhook_schedule_and_list(session_factory):
    client, sent = _client(session_factory, [{**FULL, "date": "2099-01-01"}])
    assert _post(client, _msg("schedule-event BBQ sat 1pm park"), key="a").json()["status"] == "accepted"
    assert _post(client, _msg("list-events", id="m2"), key="b").json()["status"] == "accepted"
    assert sent[0][0] == GROUP and sent[0][2] == "m1" and sent[0][1].startswith(SAVED_REPLY)
    assert "1. *Team BBQ*" in sent[1][1] and "_added by Ana_" in sent[1][1]  # "_" stripped from name


def test_webhook_rejects_bad_signature(session_factory):
    client, sent = _client(session_factory)
    assert _post(client, _msg("list-events"), secret="wrong-secret-000000").status_code == 401
    assert sent == []


def test_webhook_filters(session_factory):
    client, sent = _client(session_factory)
    assert _post(client, _msg("list-events", chatId="other@g.us"), key="1").json()["status"] == "ignored"
    assert _post(client, _msg("list-events", fromMe=True), key="2").json()["status"] == "ignored"
    assert _post(client, _msg("list-events", type="image"), key="3").json()["status"] == "ignored"
    assert _post(client, _msg("list-events"), key="4", event="message.ack").json()["status"] == "ignored"
    assert _post(client, _msg("just chatting"), key="5").json()["status"] == "no-match"
    assert sent == []


def test_webhook_dedupes_redelivery(session_factory):
    client, sent = _client(session_factory, [FULL, FULL])
    assert _post(client, _msg("schedule-event Once"), key="same").json()["status"] == "accepted"
    assert _post(client, _msg("schedule-event Once"), key="same").json()["status"] == "duplicate"
    assert len(sent) == 1
    with session_factory() as s:
        assert _count(s) == 1


def test_health_counts_all_rows(session_factory):
    client, _ = _client(session_factory)
    with session_factory() as s:
        _add(s, date="2020-01-01")
    assert client.get("/health").json()["events"] == 1
