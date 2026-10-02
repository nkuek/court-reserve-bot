"""Background loops for the WhatsApp group.

One sends queued posts once their send time passes. One relays the sign-up app's final lineups
and waitlist move-ups. One shares the group's members with the app for tagging.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from discord.ext import tasks

from utils.discord import send_discord_notification
from utils.poll_queue import due_polls, mark_attempt, mark_failed, mark_sent
from utils import signup_app
from utils.signup_app import signup_post
from utils.whatsapp import POLL_OPTIONS, group_members, is_configured, member_label, send_message, send_poll

log = logging.getLogger(__name__)

# A post still unsent this long after its send time is abandoned.
GIVE_UP_AFTER = timedelta(hours=24)


@tasks.loop(minutes=1)
async def dispatch_due_polls():
    now = datetime.now(timezone.utc)
    for poll in due_polls(now):
        question = poll["question"]
        if now - poll["send_at"] > GIVE_UP_AFTER:
            mark_failed(poll["id"], now)
            log.error(f"Giving up on WhatsApp {poll['kind']} \"{question}\" after {poll['attempts']} attempts")
            send_discord_notification(
                f"**Post:** {question}\nCould not send it for a day. Post it by hand.",
                title="WhatsApp Post Not Sent",
                success=False,
            )
            continue
        try:
            if poll["kind"] == "signup":
                text = await asyncio.to_thread(signup_post, poll["session_date"])
                message_id = await asyncio.to_thread(send_message, text, poll["target"])
            else:
                message_id = await asyncio.to_thread(send_poll, question, POLL_OPTIONS, poll["target"])
            mark_sent(poll["id"], now)
            log.info(f"Sent WhatsApp {poll['kind']} \"{question}\" ({message_id})")
        except Exception as e:
            mark_attempt(poll["id"], str(e))
            # First failure is worth a line. Later ones repeat every minute until it sends.
            if poll["attempts"] == 0:
                log.warning(f"WhatsApp {poll['kind']} \"{question}\" not sent yet: {e}")


# Posts this process already sent. If telling the app fails, the next run retries only the
# telling, so a flaky app can never make the bot repost to the group every minute.
_sent: set[tuple[str, str]] = set()
_outbox_failing = False


async def _post_once(key: tuple[str, str], text: str, mentions: list[str], mark) -> None:
    if key not in _sent:
        await asyncio.to_thread(send_message, text, None, mentions)
        _sent.add(key)
        log.info(f"Posted {key[0]} {key[1]} to the WhatsApp group with {len(mentions)} tags")
    await asyncio.to_thread(mark)


@tasks.loop(minutes=1)
async def relay_app_outbox():
    global _outbox_failing
    try:
        box = await asyncio.to_thread(signup_app.outbox)
        for item in box["lineups"]:
            sid = item["sessionId"]
            await _post_once(("lineup", sid), item["text"], item["mentions"], lambda: signup_app.mark_lineup_posted(sid))
        for item in box["notices"]:
            nid = item["id"]
            await _post_once(("notice", str(nid)), item["text"], item["mentions"], lambda: signup_app.mark_notice_sent(nid))
        _outbox_failing = False
    except Exception as e:
        # This repeats every minute while the app or sidecar is down. One line is enough.
        if not _outbox_failing:
            log.warning(f"Sign-up app outbox not relayed yet: {e}")
        _outbox_failing = True


@tasks.loop(hours=1)
async def sync_group_members():
    try:
        members = await asyncio.to_thread(group_members)
        payload = [{"id": m["id"], "label": member_label(m)} for m in members]
        count = await asyncio.to_thread(signup_app.push_members, payload)
        log.info(f"Shared {count} WhatsApp group members with the sign-up app")
    except Exception as e:
        log.warning(f"Could not share WhatsApp group members: {e}")


def start_poll_dispatcher() -> None:
    if not is_configured():
        log.info("WHATSAPP_GROUP_JID not set, poll dispatcher disabled")
        return
    if not dispatch_due_polls.is_running():
        dispatch_due_polls.start()
        log.info("WhatsApp poll dispatcher started")
    if not signup_app.is_configured():
        log.info("Sign-up app not configured, lineup relay disabled")
        return
    for loop in (relay_app_outbox, sync_group_members):
        if not loop.is_running():
            loop.start()
    log.info("Sign-up app lineup relay and member sync started")
