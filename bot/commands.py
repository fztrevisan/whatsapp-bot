"""Keyword triggers, database access for events, and reply formatting.

The regex only decides *whether* a message is a command. For `schedule-event`, the AI extractor
(bot/extraction.py) turns the free text into structured details.

WhatsApp formatting used in replies:  *bold*  _italic_  `inline code`  "1. " numbered list
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import LLMError
from .extraction import EventDetails
from .models import Event

log = logging.getLogger("openwa-bot")

SCHEDULE_CMD = "schedule-event"
LIST_CMD = "list-events"

SAVED_REPLY = (
    "Cool, your event has been saved. "
    f"Type `{LIST_CMD}` to check all existing events."
)
LIST_HEADER = "OK, here are your scheduled events:"
EMPTY_LIST = f"_No upcoming events. Add one with_ `{SCHEDULE_CMD} <text>`"
USAGE_REPLY = (
    f"Please describe the event after the keyword, including at least the *date* and *location*, e.g.\n"
    f"`{SCHEDULE_CMD} Team BBQ next Saturday 1pm at Parque Ibirapuera, R$ 30 per person`"
)
AI_ERROR_REPLY = "Sorry, I couldn't process that event right now. Please try again in a moment."

# Keyword must start the message; case-insensitive; the text may span several lines.
_SCHEDULE_RE = re.compile(rf"^\s*{re.escape(SCHEDULE_CMD)}(?:\s+(?P<text>.*))?$", re.IGNORECASE | re.DOTALL)
_LIST_RE = re.compile(rf"^\s*{re.escape(LIST_CMD)}\s*$", re.IGNORECASE)


# --- commands -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Command:
    kind: str       # "schedule" | "list"
    text: str = ""  # free text after schedule-event


def parse_command(body: str | None) -> Command | None:
    """Cheap regex check, run inline in the webhook. Returns None for normal chat messages."""
    if not body:
        return None
    if _LIST_RE.match(body):
        return Command("list")
    m = _SCHEDULE_RE.match(body)
    if m:
        return Command("schedule", (m.group("text") or "").strip())
    return None


# --- storage --------------------------------------------------------------------------------------

def add_event(session: Session, details: EventDetails, author: str) -> Event:
    """Save an extracted event. The caller has already checked that date and location are present."""
    event = Event(
        date=details.date,
        location=details.location,
        author=author,
        name=details.name or None,
        time=details.time,
        prices=list(details.prices) or None,
        extra_info=details.extra_info or None,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def list_events(session: Session, today: date) -> list[Event]:
    """Upcoming events (date >= today), soonest first; events without a time go after timed ones."""
    stmt = (
        select(Event)
        .where(Event.date >= today)
        .order_by(Event.date, Event.time.is_(None), Event.time, Event.id)
    )
    return list(session.scalars(stmt))


# --- formatting -----------------------------------------------------------------------------------

def _when(d: EventDetails | Event) -> str:
    if d.date is None:
        return "_TBD_"
    text = d.date.strftime("%a, %b %d, %Y")
    if d.time is not None:
        text += f" · {d.time:%H:%M}"
    return text


def format_details(d: EventDetails | Event, indent: str = "") -> list[str]:
    lines = [
        f"{indent}_Date:_ {_when(d)}",
        f"{indent}_Location:_ {d.location or '_TBD_'}",
    ]
    if d.prices:
        lines.append(f"{indent}_Prices:_ {' | '.join(d.prices)}")
    if d.extra_info:
        lines.append(f"{indent}_Info:_ {d.extra_info}")
    return lines


def format_saved(d: EventDetails) -> str:
    return "\n".join([SAVED_REPLY, "", f"*{d.name}*", *format_details(d)])


def format_missing(d: EventDetails) -> str:
    fields = " and ".join(f"*{m}*" for m in d.missing)
    return (
        f"I couldn't find the {fields} for this event, so it was *not* saved.\n"
        f"Please send it again including {'them' if len(d.missing) > 1 else 'it'}, e.g.\n"
        f"`{SCHEDULE_CMD} Team BBQ on Oct 12 at 7pm, Bar do Zé`"
    )


def format_event_list(events: list[Event]) -> str:
    """Format events in the order given (list_events already sorts them)."""
    if not events:
        return f"*{LIST_HEADER}*\n\n{EMPTY_LIST}"
    lines = [f"*{LIST_HEADER}*"]
    for i, ev in enumerate(events, start=1):
        lines.append("")
        lines.append(f"{i}. *{ev.name or 'Untitled event'}*")
        lines.extend(format_details(ev, indent="    "))
        lines.append(f"    _added by {ev.author}_")
    lines.append("")
    lines.append(f"Total: *{len(events)}* event{'s' if len(events) != 1 else ''}")
    return "\n".join(lines)


# --- execution ------------------------------------------------------------------------------------

Extractor = Callable[[str], EventDetails]


def execute(cmd: Command, author: str, session: Session, extract: Extractor, now: datetime) -> str:
    """Run a command and return the reply. May be slow for `schedule` (AI call).

    `now` is timezone-aware (EVENT_TIMEZONE), so "today" for the upcoming-events filter matches
    the group's local date rather than the database server's.
    """
    if cmd.kind == "list":
        return format_event_list(list_events(session, now.date()))

    if not cmd.text:
        return USAGE_REPLY
    try:
        details = extract(cmd.text)
    except LLMError as exc:
        log.error("Event extraction failed: %s", exc)
        return AI_ERROR_REPLY
    if details.missing:
        return format_missing(details)
    add_event(session, details, author or "someone")
    return format_saved(details)
