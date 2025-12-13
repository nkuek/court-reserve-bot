"""Utilities for waiting until a specific time."""

import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)


def wait_until(target_time: datetime) -> None:
    """
    Wait until the specified target time.

    Displays a countdown with updates every 30 seconds,
    and every second in the final 10 seconds.
    """
    delay = (target_time - datetime.now()).total_seconds()

    if delay <= 0:
        log.info("Target time already reached, continuing immediately")
        return

    hours = int(delay // 3600)
    minutes = int((delay % 3600) // 60)
    seconds = int(delay % 60)

    log.info("=" * 50)
    log.info(f"WAITING until {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Time remaining: {hours}h {minutes}m {seconds}s")
    log.info("=" * 50)

    last_log_time = None
    while True:
        remaining = (target_time - datetime.now()).total_seconds()
        if remaining <= 0:
            break

        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        seconds = int(remaining % 60)

        # Log every 30 seconds, or every second in final 10 seconds
        current_remaining_int = int(remaining)
        should_log = (
            last_log_time is None or
            remaining <= 10 or
            (current_remaining_int % 30 == 0 and current_remaining_int != last_log_time)
        )

        if should_log:
            log.info(f"  ⏳ {hours:02d}:{minutes:02d}:{seconds:02d} remaining...")
            last_log_time = current_remaining_int

        # Sleep for ~1 second, but check more frequently near the end
        sleep_time = min(1.0, remaining)
        if sleep_time > 0:
            time.sleep(sleep_time)

    log.info("✓ Wait complete, starting execution")
    log.info("=" * 50)


def parse_wait_time(value: str) -> datetime:
    """
    Parse a time or datetime string.

    Supports formats:
        - HH:MM (e.g., "07:00") - today, or tomorrow if already passed
        - HH:MM:SS (e.g., "07:00:00") - today, or tomorrow if already passed
        - YYYY-MM-DD HH:MM (e.g., "2025-12-14 07:00") - specific date
        - YYYY-MM-DD HH:MM:SS (e.g., "2025-12-14 07:00:00") - specific date
        - tomorrow HH:MM (e.g., "tomorrow 07:00") - tomorrow at time
        - +Nd HH:MM (e.g., "+1d 07:00") - N days from now at time

    Returns:
        datetime: The target time to wait until.
    """
    from datetime import timedelta

    now = datetime.now()
    value = value.strip()

    # Handle "tomorrow HH:MM" format
    if value.lower().startswith("tomorrow "):
        time_part = value[9:].strip()
        target = _parse_time_only(time_part, now)
        target += timedelta(days=1)
        return target

    # Handle "+Nd HH:MM" format (e.g., "+1d 07:00", "+5d 07:00")
    if value.startswith("+") and "d " in value.lower():
        parts = value[1:].lower().split("d ", 1)
        try:
            days = int(parts[0])
            time_part = parts[1].strip()
        except (ValueError, IndexError):
            raise ValueError(
                f"Invalid relative date format: '{value}'.\n"
                f"  Expected format: +Nd HH:MM (e.g., +1d 07:00, +5d 07:00)"
            )
        target = _parse_time_only(time_part, now)
        target += timedelta(days=days)
        return target

    # Handle "YYYY-MM-DD HH:MM" or "YYYY-MM-DD HH:MM:SS" format
    if " " in value and "-" in value.split(" ")[0]:
        try:
            if value.count(":") == 1:
                target = datetime.strptime(value, "%Y-%m-%d %H:%M")
            else:
                target = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")

            if target <= now:
                raise ValueError(
                    f"Target time '{value}' is in the past.\n"
                    f"  Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            return target
        except ValueError as e:
            if "Target time" in str(e):
                raise
            raise ValueError(
                f"Invalid datetime format: '{value}'.\n"
                f"  Expected: YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS\n"
                f"  Example: 2025-12-14 07:00"
            )

    # Handle time-only format (HH:MM or HH:MM:SS)
    target = _parse_time_only(value, now)

    # If time already passed today, schedule for tomorrow
    if target <= now:
        target += timedelta(days=1)
        log.info(f"Time {value} already passed today, waiting until tomorrow")

    return target


def _parse_time_only(value: str, base: datetime) -> datetime:
    """Parse a time-only string and return a datetime for the given base date."""
    try:
        if value.count(":") == 1:
            hour, minute = map(int, value.split(":"))
            second = 0
        else:
            hour, minute, second = map(int, value.split(":"))
    except ValueError:
        raise ValueError(
            f"Invalid time format: '{value}'.\n"
            f"  Expected HH:MM or HH:MM:SS (e.g., 07:00 or 07:00:00)"
        )

    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(
            f"Invalid time value: '{value}'.\n"
            f"  Hour must be 0-23, minute 0-59, second 0-59."
        )

    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)
