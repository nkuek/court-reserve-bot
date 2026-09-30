"""Background loop that sends queued WhatsApp polls once their send time passes."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from discord.ext import tasks

from utils.discord import send_discord_notification
from utils.poll_queue import due_polls, mark_attempt, mark_failed, mark_sent
from utils.whatsapp import is_configured, send_poll

log = logging.getLogger(__name__)

# A poll still unsent this long after its send time is abandoned.
GIVE_UP_AFTER = timedelta(hours=24)


@tasks.loop(minutes=1)
async def dispatch_due_polls():
    now = datetime.now(timezone.utc)
    for poll in due_polls(now):
        question = poll["question"]
        if now - poll["send_at"] > GIVE_UP_AFTER:
            mark_failed(poll["id"], now)
            log.error(f"Giving up on WhatsApp poll \"{question}\" after {poll['attempts']} attempts")
            send_discord_notification(
                f"**Poll:** {question}\nCould not reach the WhatsApp sidecar for a day. Post it by hand.",
                title="WhatsApp Poll Not Sent",
                success=False,
            )
            continue
        try:
            message_id = await asyncio.to_thread(send_poll, question)
            mark_sent(poll["id"], now)
            log.info(f"Sent WhatsApp poll \"{question}\" ({message_id})")
        except Exception as e:
            mark_attempt(poll["id"], str(e))
            # First failure is worth a line. Later ones repeat every minute until it sends.
            if poll["attempts"] == 0:
                log.warning(f"WhatsApp poll \"{question}\" not sent yet: {e}")


def start_poll_dispatcher() -> None:
    if not is_configured():
        log.info("WHATSAPP_GROUP_JID not set, poll dispatcher disabled")
        return
    if not dispatch_due_polls.is_running():
        dispatch_due_polls.start()
        log.info("WhatsApp poll dispatcher started")
