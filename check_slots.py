#!/usr/bin/env python3
"""
Check available court slots for a given date.

Usage:
    python check_slots.py --date today
    python check_slots.py --date tomorrow
    python check_slots.py --date latest
"""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from constants import get_page, close_browser
from utils.login import login
from utils.booking_date import select_booking_date, parse_booking_date


# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

app = typer.Typer(help="Check available court slots on CourtReserve.")


def get_available_slots() -> dict[str, list[str]]:
    """
    Get all available time slots for each court.

    Returns:
        A dictionary mapping court names to lists of available times.
        Example: {"Pickleball Court 5C (Bubble B)": ["9:00 AM", "9:30 AM", ...]}
    """
    page = get_page()

    log.info("Scanning for available time slots...")

    # Wait for the scheduler to fully load
    log.info("Waiting for scheduler to load...")
    try:
        page.wait_for_selector(".k-scheduler-content", timeout=15000)
        log.info("  Scheduler content found")
    except Exception as e:
        log.warning(f"  Scheduler load timeout: {e}")

    # Additional wait for dynamic content
    page.wait_for_timeout(3000)

    # Find available slot buttons
    # Available slots have the "slot-btn" class WITHOUT the "hide" class
    # Reserved slots have buttons with the "hide" class (should be excluded)
    log.info("Searching for available slot buttons...")

    # Look for buttons that:
    # 1. Have data-courtlabel attribute
    # 2. Are not disabled
    # 3. Have the slot-btn class (available slots)
    # 4. Do NOT have the "hide" class (reserved slots have this)
    slot_buttons = page.query_selector_all("button.slot-btn[data-courtlabel]:not([disabled]):not(.hide)")

    log.info(f"Found {len(slot_buttons)} available slot buttons (excluding hidden)")

    # Debug: Check how many total and hidden buttons exist
    all_slot_buttons = page.query_selector_all("button.slot-btn[data-courtlabel]")
    hidden_buttons = page.query_selector_all("button.slot-btn[data-courtlabel].hide")
    log.info(f"  Total slot buttons: {len(all_slot_buttons)}, Hidden (reserved): {len(hidden_buttons)}")

    # If no buttons found with slot-btn class, try broader search for debugging
    if len(slot_buttons) == 0:
        log.warning("No visible slot-btn buttons found, trying broader search...")
        all_buttons = page.query_selector_all("button[data-courtlabel]")
        log.info(f"  Found {len(all_buttons)} total buttons with data-courtlabel")

        # Check what classes these buttons have
        if all_buttons:
            sample = all_buttons[0]
            classes = sample.get_attribute("class") or ""
            text = (sample.text_content() or "").strip()[:50]
            log.info(f"  Sample button - class: '{classes}', text: '{text}'")

        # Fall back to broader selector (still excluding hide)
        slot_buttons = page.query_selector_all("button[data-courtlabel]:not([disabled]):not(.hide)")
        log.info(f"  Using fallback: found {len(slot_buttons)} buttons")

    # Group by court
    slots_by_court: dict[str, list[str]] = defaultdict(list)
    parsed_count = 0
    skipped_count = 0

    log.info("Parsing slot buttons...")
    for button in slot_buttons:
        try:
            court = button.get_attribute("data-courtlabel")
            time_text = (button.text_content() or "").strip()

            if court and time_text:
                # Extract time from button text (e.g., "Reserve2:00PM" -> "2:00 PM")
                # Remove "Reserve" prefix if present
                original_text = time_text
                time_text = time_text.replace("Reserve", "").strip()

                # Only add if it looks like a time (contains AM/PM)
                if "AM" in time_text.upper() or "PM" in time_text.upper():
                    # Normalize format: "2:00PM" -> "2:00 PM"
                    time_text = time_text.replace("AM", " AM").replace("PM", " PM")
                    time_text = time_text.replace("  ", " ").strip()  # Clean double spaces
                    slots_by_court[court].append(time_text)
                    parsed_count += 1
                    log.debug(f"  Parsed: {court} @ {time_text} (raw: '{original_text}')")
                else:
                    skipped_count += 1
                    log.debug(f"  Skipped non-time button: '{time_text}'")
            else:
                skipped_count += 1
        except Exception as e:
            log.warning(f"Error parsing button: {e}")
            skipped_count += 1
            continue

    log.info(f"Parsed {parsed_count} time slots, skipped {skipped_count} buttons")
    log.info(f"Found slots across {len(slots_by_court)} courts")

    # Sort times for each court
    def parse_time(t: str) -> datetime:
        """Parse time string to datetime for sorting."""
        # Normalize: remove spaces and ensure consistent format
        normalized = t.replace(" ", "").upper()
        try:
            return datetime.strptime(normalized, "%I:%M%p")
        except ValueError:
            # Fallback for edge cases
            log.warning(f"Could not parse time for sorting: '{t}'")
            return datetime.min

    log.info("Sorting time slots...")
    for court in slots_by_court:
        slots_by_court[court] = sorted(slots_by_court[court], key=parse_time)
        short_name = court.replace("Pickleball Court ", "").replace(" (Bubble B)", "")
        log.info(f"  Court {short_name}: {len(slots_by_court[court])} slots")

    return dict(slots_by_court)


def parse_time_str(t: str) -> datetime:
    """Parse a time string like '6:00 AM' to a datetime object."""
    normalized = t.replace(" ", "").upper()
    try:
        return datetime.strptime(normalized, "%I:%M%p")
    except ValueError:
        return datetime.min


def group_consecutive_times(times: list[str], slot_duration_minutes: int = 30) -> list[str]:
    """
    Group consecutive time slots into ranges.

    Args:
        times: List of time strings like ["6:00 AM", "6:30 AM", "7:00 AM", "9:00 AM"]
        slot_duration_minutes: Duration of each time slot (default 30 minutes)

    Returns:
        List of range strings like ["6:00 AM – 7:00 AM", "9:00 AM"]
    """
    if not times:
        return []

    if len(times) == 1:
        return times

    # Parse all times and pair with original strings
    parsed = [(parse_time_str(t), t) for t in times]
    parsed.sort(key=lambda x: x[0])

    ranges = []
    range_start = parsed[0][1]
    range_end = parsed[0][1]
    prev_time = parsed[0][0]

    for i in range(1, len(parsed)):
        curr_time, curr_str = parsed[i]
        expected_next = prev_time + timedelta(minutes=slot_duration_minutes)

        # Check if this time is consecutive (within slot duration)
        if curr_time == expected_next:
            # Extend the current range
            range_end = curr_str
        else:
            # End current range and start a new one
            if range_start == range_end:
                ranges.append(range_start)
            else:
                ranges.append(f"{range_start} – {range_end}")
            range_start = curr_str
            range_end = curr_str

        prev_time = curr_time

    # Don't forget the last range
    if range_start == range_end:
        ranges.append(range_start)
    else:
        ranges.append(f"{range_start} – {range_end}")

    return ranges


def format_slots_for_display(slots: dict[str, list[str]]) -> str:
    """Format slots dictionary as a nice display string."""
    if not slots:
        return "No available slots found."

    lines = []

    # Sort courts by name
    for court in sorted(slots.keys()):
        times = slots[court]
        # Simplify court name for display
        short_name = court.replace("Pickleball Court ", "Court ")
        lines.append(f"**{short_name}**")

        if times:
            # Group consecutive times into ranges
            ranges = group_consecutive_times(times)
            lines.append(f"  {', '.join(ranges)}")
        else:
            lines.append("  No slots available")
        lines.append("")

    return "\n".join(lines)


@app.command()
def main(
    date: Annotated[
        str,
        typer.Option(
            "--date", "-d",
            help="Date to check. Formats: today, tomorrow, +3d, 12/15, latest (default)",
        ),
    ] = "latest",
    email: Annotated[
        str | None,
        typer.Option(
            "--email",
            help="CourtReserve email (overrides .env)",
            envvar="EMAIL",
        ),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="CourtReserve password (overrides .env)",
            envvar="PASSWORD",
        ),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option(
            "--format", "-f",
            help="Output format: text or json",
        ),
    ] = "text",
):
    """Check available court slots for a given date."""
    # Set credentials in environment for login module
    if email:
        os.environ["EMAIL"] = email
    if password:
        os.environ["PASSWORD"] = password

    # Parse the booking date
    booking_date = parse_booking_date(date)
    booking_date_str = booking_date.strftime("%a %m/%d")  # e.g., "Sat 12/14"

    log.info("=" * 50)
    log.info("CHECKING AVAILABLE SLOTS")
    log.info(f"  Date: {booking_date_str}")
    log.info("=" * 50)

    log.info("Initializing browser...")
    page = get_page()
    log.info("Browser ready")

    try:
        login()
        select_booking_date(booking_date)

        log.info("")
        slots = get_available_slots()
        log.info("")

        # Convert slots to ranges for display
        ranges_by_court = {
            court: group_consecutive_times(times)
            for court, times in slots.items()
        }

        if output_format.lower() == "json":
            # Output as JSON for bot to parse
            result = {
                "date": booking_date_str,
                "slots": slots,  # Raw slots for reference
                "ranges": ranges_by_court,  # Grouped ranges for display
                "total_slots": sum(len(times) for times in slots.values()),
            }
            print("===JSON_START===")
            print(json.dumps(result, indent=2))
            print("===JSON_END===")
        else:
            # Human-readable output
            log.info("")
            log.info(f"Available slots for {booking_date_str}:")
            log.info("")

            if not slots:
                log.info("  No slots available!")
            else:
                total_slots = 0
                for court in sorted(slots.keys()):
                    times = slots[court]
                    ranges = ranges_by_court[court]
                    total_slots += len(times)
                    short_name = court.replace("Pickleball Court ", "Court ")
                    log.info(f"  {short_name}:")
                    log.info(f"    {', '.join(ranges)}")
                    log.info("")

                log.info(f"Total: {total_slots} slots across {len(slots)} courts")

        log.info("=" * 50)
        log.info("DONE")
        log.info("=" * 50)

    except Exception as e:
        log.error(f"Error checking slots: {e}")
        raise
    finally:
        close_browser()


def run():
    """Entry point with error handling."""
    try:
        app()
    except SystemExit:
        raise
    except Exception as e:
        error_type = type(e).__name__
        log.error(f"Check failed ({error_type}): {e}")
        close_browser()
        sys.exit(1)


if __name__ == "__main__":
    run()
