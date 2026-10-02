"""Adds booked courts to the pickleball sign-up app and fetches its WhatsApp post."""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta


def is_configured() -> bool:
    return all(os.environ.get(k) for k in ("SIGNUP_APP_URL", "SIGNUP_BOT_TOKEN", "SIGNUP_BOOKER_NAME"))


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = os.environ["SIGNUP_APP_URL"].rstrip("/") + "/api" + path
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['SIGNUP_BOT_TOKEN']}",
            "Content-Type": "application/json",
            # Cloudflare turns away requests that carry urllib's default user agent.
            "User-Agent": "court-reserve-bot",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def add_court(start: datetime, duration_hours: float, court_short: str) -> None:
    """List a booked court in the app. The booker is signed up and placed on that court."""
    end = start + timedelta(hours=duration_hours)
    _request("POST", "/bot/courts", {
        "date": f"{start:%Y-%m-%d}",
        "court": court_short,
        "name": os.environ["SIGNUP_BOOKER_NAME"],
        "play": True,
        "start": f"{start:%H:%M}",
        "end": f"{end:%H:%M}",
    })


def signup_post(date: str) -> str:
    """The day's sign-up post as it reads right now, link and passcode included."""
    return _request("GET", "/bot/post?" + urllib.parse.urlencode({"date": date}))["text"]


def push_members(members: list[dict]) -> int:
    """Share the WhatsApp group's members, so the organizer can link names to them."""
    return _request("POST", "/bot/members", {"members": members})["count"]


def outbox() -> dict:
    """Final lineups and waitlist move-ups that are ready to post, each with its mention IDs."""
    return _request("GET", "/bot/outbox")


def mark_lineup_posted(session_id: str) -> None:
    _request("POST", f"/bot/lineups/{session_id}/posted", {})


def mark_notice_sent(notice_id: int) -> None:
    _request("POST", f"/bot/notices/{notice_id}/sent", {})
