"""Posts bookings to the WhatsApp group through the sidecar in whatsapp/."""

import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta

from constants import LOCAL_TZ
from utils import signup_app
from utils.discord import send_discord_notification
from utils.poll_queue import enqueue_post

log = logging.getLogger(__name__)

POLL_OPTIONS = ["Yes", "No"]

# Local hour on the day after a booking lands when its post goes out.
SEND_HOUR = 12

# Local hour on the day before a session when its sign-ups close and courts get drawn.
SIGNUPS_CLOSE_HOUR = 12


def _sidecar_url() -> str:
    return os.environ.get("WHATSAPP_SIDECAR_URL", "http://127.0.0.1:8765").rstrip("/")


def is_configured() -> bool:
    return bool(os.environ.get("WHATSAPP_GROUP_JID"))


def court_short_name(court_name: str) -> str:
    """'Pickleball Court #8A (Bubble B)' -> '8A'."""
    parts = court_name.split()
    token = parts[2] if len(parts) > 2 else court_name
    return token.lstrip("#")


def next_day_send_time(now: datetime) -> datetime:
    local = now.astimezone(LOCAL_TZ)
    return (local + timedelta(days=1)).replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)


def post_send_time(now: datetime, start: datetime) -> datetime:
    """Noon the day after booking, or right away when that would land after sign-ups close."""
    send_at = next_day_send_time(now)
    close = (start - timedelta(days=1)).replace(hour=SIGNUPS_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    return send_at if send_at < close else now


def queue_booking_post(booking_date: datetime, hour: int, minute: int, duration_hours: float, court_name: str) -> None:
    """List the court in the sign-up app and queue the group's sign-up post.

    Never raises: the booking already succeeded.
    """
    if not signup_app.is_configured():
        log.info("Sign-up app not configured, skipping the group post")
        return
    start = booking_date.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=LOCAL_TZ)
    court = court_short_name(court_name)
    try:
        signup_app.add_court(start, duration_hours, court)
        log.info(f"Listed court {court} on {start:%a %m/%d} in the sign-up app")
    except Exception as e:
        log.warning(f"Failed to list court {court} in the sign-up app: {e}")
        send_discord_notification(
            f"**Court:** {court} on {start:%a %m/%d}\nCould not reach the sign-up app. Add it there by hand.",
            title="Sign-up App Not Updated",
            success=False,
        )
        return
    if not is_configured():
        log.info("WHATSAPP_GROUP_JID not set, skipping the group post")
        return
    try:
        send_at = post_send_time(datetime.now(LOCAL_TZ), start)
        # One post per day: a second court that day joins the same post.
        if enqueue_post(f"{start:%Y-%m-%d}", send_at):
            log.info(f"Queued sign-up post for {start:%a %m/%d}, sending {send_at:%a %m/%d %-I:%M %p}")
        else:
            log.info(f"Sign-up post for {start:%a %m/%d} already queued")
    except Exception as e:
        log.warning(f"Failed to queue sign-up post: {e}")


def send_poll(question: str, options: list[str] = POLL_OPTIONS, to: str | None = None) -> str | None:
    """Post a poll through the sidecar. Raises on any transport or sidecar error."""
    body = {"question": question, "options": options, "selectableCount": 1}
    if to:
        body["to"] = to
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{_sidecar_url()}/send-poll",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode()).get("id")


def send_message(
    text: str, to: str | None = None, mentions: list[str] | None = None, pin_seconds: int = 0
) -> str | None:
    """Post a text message through the sidecar. Raises on any transport or sidecar error.

    Each mention ID needs a matching "@<id number>" in the text to tag that person. A pin that
    fails is logged, since the message itself went out.
    """
    body = {"text": text, "mentions": mentions or []}
    if to:
        body["to"] = to
    if pin_seconds:
        body["pinSeconds"] = pin_seconds
    req = urllib.request.Request(
        f"{_sidecar_url()}/send-message",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
    if pin_seconds and not result.get("pinned"):
        log.warning(f"Sent but could not pin: {result.get('pinError', 'sidecar did not pin')}")
    return result.get("id")


def group_members() -> list[dict]:
    """The configured group's members as {id, phone, name}, phone and name when known."""
    with urllib.request.urlopen(f"{_sidecar_url()}/group-members", timeout=15) as resp:
        return json.loads(resp.read().decode())


def member_label(member: dict) -> str:
    """A WhatsApp name, or the number's last four digits. Full numbers never leave this machine."""
    if member.get("name"):
        return member["name"]
    if member.get("phone"):
        return f"••{member['phone'][-4:]}"
    return "Unknown member"


def sidecar_health() -> dict:
    with urllib.request.urlopen(f"{_sidecar_url()}/health", timeout=5) as resp:
        return json.loads(resp.read().decode())
