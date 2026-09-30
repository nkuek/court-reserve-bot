"""Formats booking polls and talks to the WhatsApp sidecar in whatsapp/."""

import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta

from constants import LOCAL_TZ
from utils.poll_queue import enqueue_poll

log = logging.getLogger(__name__)

POLL_OPTIONS = ["Yes", "No"]

# Local hour on the day after a booking lands when its poll goes out.
SEND_HOUR = 12


def _sidecar_url() -> str:
    return os.environ.get("WHATSAPP_SIDECAR_URL", "http://127.0.0.1:8765").rstrip("/")


def is_configured() -> bool:
    return bool(os.environ.get("WHATSAPP_GROUP_JID"))


def court_short_name(court_name: str) -> str:
    """'Pickleball Court #8A (Bubble B)' -> '8A'."""
    parts = court_name.split()
    token = parts[2] if len(parts) > 2 else court_name
    return token.lstrip("#")


def _clock(dt: datetime, with_meridiem: bool) -> str:
    text = dt.strftime("%-I") if dt.minute == 0 else dt.strftime("%-I:%M")
    return text + dt.strftime("%p") if with_meridiem else text


def format_time_range(start: datetime, duration_hours: float) -> str:
    """'8-11PM' when both ends share a meridiem, otherwise '11AM-1PM'."""
    end = start + timedelta(hours=duration_hours)
    same_meridiem = start.strftime("%p") == end.strftime("%p")
    return f"{_clock(start, not same_meridiem)}-{_clock(end, True)}"


def format_poll_question(start: datetime, duration_hours: float, court_name: str) -> str:
    """Matches the hand-written polls: 'Monday 10/5 8-11PM on 8A'."""
    date = f"{start.month}/{start.day}"
    return f"{start:%A} {date} {format_time_range(start, duration_hours)} on {court_short_name(court_name)}"


def next_day_send_time(now: datetime) -> datetime:
    local = now.astimezone(LOCAL_TZ)
    return (local + timedelta(days=1)).replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)


def queue_booking_poll(booking_date: datetime, hour: int, minute: int, duration_hours: float, court_name: str) -> None:
    """Queue the group poll for a booked slot. Never raises: the booking already succeeded."""
    if not is_configured():
        log.info("WHATSAPP_GROUP_JID not set, skipping poll")
        return
    try:
        start = booking_date.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=LOCAL_TZ)
        question = format_poll_question(start, duration_hours, court_name)
        send_at = next_day_send_time(datetime.now(LOCAL_TZ))
        if enqueue_poll(question, send_at):
            log.info(f"Queued WhatsApp poll \"{question}\" for {send_at:%a %m/%d %-I:%M %p}")
        else:
            log.info(f"WhatsApp poll \"{question}\" already queued")
    except Exception as e:
        log.warning(f"Failed to queue WhatsApp poll: {e}")


def send_poll(question: str, options: list[str] = POLL_OPTIONS) -> str | None:
    """Post a poll through the sidecar. Raises on any transport or sidecar error."""
    payload = json.dumps({"question": question, "options": options, "selectableCount": 1}).encode()
    req = urllib.request.Request(
        f"{_sidecar_url()}/send-poll",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode()).get("id")


def sidecar_health() -> dict:
    with urllib.request.urlopen(f"{_sidecar_url()}/health", timeout=5) as resp:
        return json.loads(resp.read().decode())
