import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def send_discord_notification(
    message: str,
    title: str = "Court Booking Bot",
    success: bool = True,
    log_content: Optional[str] = None,
):
    """
    Send a notification to Discord via webhook.

    Args:
        message: The message content
        title: Embed title
        success: True for green color, False for red
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        log.warning("DISCORD_WEBHOOK_URL not set, skipping notification")
        return

    # Color: green for success, red for failure
    color = 0x00FF00 if success else 0xFF0000

    # Try to upload log to 0x0.st if provided
    log_url = None
    if log_content:
        try:
            boundary = "----courtreserveboundary"
            data_parts = [
                f"--{boundary}",
                'Content-Disposition: form-data; name="file"; filename="log.txt"',
                "Content-Type: text/plain",
                "",
                log_content,
                f"--{boundary}--",
                "",
            ]
            data = "\r\n".join(data_parts).encode("utf-8")
            req = urllib.request.Request(
                "https://0x0.st",
                data=data,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": "CourtBookingBot/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    log_url = resp.read().decode("utf-8").strip()
        except Exception as e:
            log.warning(f"Failed to upload log to 0x0.st: {e}")

    # Build the embed
    payload = {
        "embeds": [
            {
                "title": f"{'✅' if success else '❌'} {title}",
                "description": f"{message}\n\nLog: {log_url}" if log_url else message,
                "color": color,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {
                    "text": "Court Booking Bot"
                }
            }
        ]
    }

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'CourtBookingBot/1.0'
            },
            method='POST'
        )
        urllib.request.urlopen(req, timeout=10)
        log.info("Discord notification sent")
    except urllib.error.HTTPError as e:
        log.error(f"Discord notification failed: HTTP {e.code} - {e.reason}")
        if e.code == 403:
            log.error("  Webhook URL may be invalid or expired. Create a new webhook in Discord.")
        elif e.code == 404:
            log.error("  Webhook not found. Check your DISCORD_WEBHOOK_URL in .env")
    except Exception as e:
        log.error(f"Failed to send Discord notification: {e}")


def notify_success(court: str, date: str, time: str, duration: float):
    """Send a success notification for court booking."""
    message = f"**Court:** {court}\n**Date:** {date}\n**Time:** {time}\n**Duration:** {duration} hours"
    send_discord_notification(message, title="Court Booked Successfully!", success=True)


def notify_failure(error: str):
    """Send a failure notification."""
    send_discord_notification(f"**Error:** {error}", title="Booking Failed", success=False)


def notify_open_play_success(event: str, date: str):
    """Send a success notification for open play registration."""
    message = f"**Event:** {event}\n**Date:** {date}"
    send_discord_notification(message, title="Open Play Registration Successful!", success=True)


def notify_start(script_name: str, details: str = ""):
    """Send a notification that a script has started."""
    message = f"**Script:** {script_name}"
    if details:
        message += f"\n{details}"
    send_discord_notification(message, title="🚀 Bot Started", success=True)
