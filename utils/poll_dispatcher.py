"""Background loop that sends queued WhatsApp posts once their send time passes."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from discord.ext import tasks

from utils.discord import send_discord_notification
from utils.poll_queue import due_polls, mark_attempt, mark_failed, mark_sent
from utils.signup_app import signup_post
from utils.whatsapp import POLL_OPTIONS, is_configured, send_message, send_poll

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


def start_poll_dispatcher() -> None:
    if not is_configured():
        log.info("WHATSAPP_GROUP_JID not set, poll dispatcher disabled")
        return
    if not dispatch_due_polls.is_running():
        dispatch_due_polls.start()
        log.info("WhatsApp poll dispatcher started")
