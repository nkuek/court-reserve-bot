#!/usr/bin/env python3
"""
Court booking script for CourtReserve.

Usage:
    python court_booking.py --time 21:00 --duration 2
"""

import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from constants import (
    get_page, close_browser, COURTS, VALID_DURATIONS,
    FACILITY_CLOSING_HOUR, FACILITY_CLOSING_MINUTE
)
from utils.login import login
from utils.booking_date import select_booking_date, parse_booking_date
from utils.discord import notify_success, notify_failure, notify_start
from utils.exceptions import CourtUnavailableError
from utils.wait import wait_until, parse_wait_time


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

app = typer.Typer(help="Book a pickleball court on CourtReserve.")


def validate_time(value: str) -> str:
    """Validate and return the time string in HH:MM format."""
    if not re.match(r"^\d{1,2}:\d{2}$", value):
        raise typer.BadParameter(
            f"Invalid format '{value}'. Expected HH:MM (e.g., 21:00 for 9:00 PM)"
        )

    hour, minute = map(int, value.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise typer.BadParameter(
            f"Invalid time '{value}'. Hour must be 0-23, minute must be 0-59."
        )

    return value


def validate_duration(value: float) -> float:
    """Validate the duration is one of the allowed values."""
    if value not in VALID_DURATIONS:
        raise typer.BadParameter(
            f"Invalid duration: {value}. Must be one of: {', '.join(map(str, VALID_DURATIONS))}"
        )
    return value


def to_12_hour(time_str: str | datetime) -> str:
    """Convert 24-hour time to 12-hour format."""
    if isinstance(time_str, str):
        hour, minute = map(int, time_str.split(":"))
        dt = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        dt = time_str

    return dt.strftime("%-I:%M %p")  # e.g., "9:00 PM"


def duration_to_index(hours: float) -> int:
    """Convert duration hours to dropdown index."""
    # 1 hour is selected by default, so subtract 1
    index = VALID_DURATIONS.index(hours) - 1
    return index if index >= 0 else -1


def add_players():
    """Add 3 placeholder players to the reservation."""
    log.info("Adding placeholder players...")
    page = get_page()

    for i in range(3):
        additional_players_input = page.locator("[name='OwnersDropdown_input']")
        additional_players_input.wait_for(timeout=10000)
        additional_players_input.fill("Placeholder")

        # Wait for results to appear
        page.locator("#OwnersDropdown_listbox li").first.wait_for(timeout=5000)
        page.wait_for_timeout(1000)

        # Select first option via keyboard
        additional_players_input.press("ArrowDown")
        additional_players_input.press("Enter")
        log.info(f"  Added placeholder {i + 1}/3")

    log.info("Successfully added all placeholders")


def click_disclosure():
    """Click the disclosure agreement checkbox."""
    log.info("Clicking disclosure checkbox...")
    page = get_page()
    disclosure_label = page.locator("label[for='DisclosureAgree']")
    disclosure_label.wait_for(timeout=10000)
    disclosure_label.click()
    log.info("Disclosure accepted")
    page.wait_for_timeout(1000)


def add_duration(duration_hours: float):
    """Set the reservation duration."""
    log.info(f"Setting duration to {duration_hours} hours...")
    page = get_page()

    duration_input = page.locator("span[aria-owns='Duration_listbox']")
    duration_input.wait_for(timeout=10000)
    duration_input.click()

    # Wait for dropdown to open
    page.locator('ul[data-testid="Duration-container"][aria-hidden="false"]').wait_for(timeout=5000)

    duration_index = duration_to_index(duration_hours)

    for _ in range(duration_index + 1):
        duration_input.press("ArrowDown")

    duration_input.press("Enter")
    log.info(f"Duration set to {duration_hours} hours")


def click_save_button(target_time: datetime):
    """Wait until target time and click the save button with high precision."""
    page = get_page()
    log.info("Preparing to click Save button...")
    save_button = page.locator('button[data-testid="Save"]')
    save_button.wait_for(timeout=10000)

    delay = (target_time - datetime.now()).total_seconds()

    if delay < 0:
        log.warning("Target time already passed, clicking Save immediately")
    else:
        hours = int(delay // 3600)
        minutes = int((delay % 3600) // 60)
        seconds = int(delay % 60)
        log.info(f"Waiting {hours}h {minutes}m {seconds}s until target time ({target_time.strftime('%H:%M:%S')})")

        # Log countdown every 30 seconds (or every second if < 30s remaining)
        last_log_time = None
        while True:
            remaining = (target_time - datetime.now()).total_seconds()
            if remaining <= 0.1:
                break

            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            seconds = int(remaining % 60)

            # Log every 30 seconds, or every second in final 10 seconds
            current_remaining_int = int(remaining)
            should_log = (
                last_log_time is None or
                remaining <= 10 or
                current_remaining_int % 30 == 0 and current_remaining_int != last_log_time
            )

            if should_log:
                log.info(f"  {hours:02d}:{minutes:02d}:{seconds:02d} remaining...")
                last_log_time = current_remaining_int

            # Sleep for ~1 second, but check more frequently near the end
            sleep_time = min(1.0, remaining - 0.1)
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Busy-wait (spin loop) for precise timing in the final milliseconds
        while datetime.now() < target_time:
            pass

    # Use JavaScript click for faster execution
    save_button.evaluate("el => el.click()")

    click_time = datetime.now()
    diff_ms = (click_time - target_time).total_seconds() * 1000
    log.info(f"CLICKED Save button at {click_time.strftime('%H:%M:%S.%f')[:-3]} (diff: {diff_ms:+.1f}ms)")
    page.wait_for_timeout(1000)


def check_court_availability(court: str, reservation_time: str, end_time: str):
    """Check if a court is available at the specified time."""
    log.info(f"Checking court: {court} for {reservation_time}")
    page = get_page()

    try:
        start_time_btn = page.locator(
            f"button[data-courtlabel='{court}']:has-text('{reservation_time}')"
        )
        start_time_btn.wait_for(timeout=5000)
        log.info(f"  Found start time {reservation_time}")
    except Exception:
        raise CourtUnavailableError(
            f"Start time {reservation_time} not available on {court}.\n"
            f"  The time slot may already be booked."
        )

    try:
        # Verify the time slot is available by checking the end time
        end_time_btn = page.locator(
            f"button[data-courtlabel='{court}']:has-text('{end_time}')"
        )
        end_time_btn.wait_for(timeout=5000)
        log.info(f"  Found end time {end_time} - slot available!")
    except Exception:
        raise CourtUnavailableError(
            f"Full duration not available on {court}.\n"
            f"  Start time {reservation_time} found, but end time {end_time} is blocked.\n"
            f"  Another reservation may overlap with your requested time slot."
        )

    start_time_btn.click()


def get_available_courts(reservation_time: str, end_time: str, courts: list[str] = None) -> list[str]:
    """
    Find which courts have the requested time slot available.

    Uses get_available_slots() from check_slots.py to get all available slots,
    then filters to courts that have both start and end time.

    Args:
        reservation_time: Start time in 12-hour format (e.g., "10:00 AM")
        end_time: End slot time in 12-hour format (e.g., "10:30 AM" for 1 hour booking)
        courts: List of courts to check (defaults to all COURTS)

    Returns:
        List of court names that have the full duration available.
    """
    from check_slots import get_available_slots

    courts_to_check = courts or COURTS

    log.info(f"\nScanning availability for {reservation_time}...")

    # Get all available slots using the proven check_slots logic
    slots_by_court = get_available_slots()

    # Check which courts have both start and end time
    available = []
    for court_name in courts_to_check:
        court_slots = slots_by_court.get(court_name, [])
        has_start = reservation_time in court_slots
        has_end = end_time in court_slots

        if has_start and has_end:
            available.append(court_name)
            log.info(f"  ✓ {court_name}: Available")
        elif has_start:
            log.info(f"  ✗ {court_name}: Not available (duration blocked)")
        else:
            log.info(f"  ✗ {court_name}: Not available")

    log.info(f"\nFound {len(available)} available court(s)")
    return available


@app.command()
def main(
    time_str: Annotated[
        str,
        typer.Option(
            "--time", "-t",
            help="Reservation time in 24h format (e.g., 21:00 for 9:00 PM)",
            callback=validate_time,
        ),
    ],
    duration: Annotated[
        float,
        typer.Option(
            "--duration", "-d",
            help="Duration in hours (1, 1.5, 2, 2.5, or 3)",
            callback=validate_duration,
        ),
    ],
    wait_until_time: Annotated[
        str | None,
        typer.Option(
            "--wait-until", "-w",
            help="Wait until this time before starting. Formats: 07:00, tomorrow 07:00, +1d 07:00, 2025-12-14 07:00",
        ),
    ] = None,
    date: Annotated[
        str,
        typer.Option(
            "--date",
            help="Date to book. Formats: today, tomorrow, +3d, 12/15, latest (default)",
        ),
    ] = "latest",
    court: Annotated[
        str | None,
        typer.Option(
            "--court", "-c",
            help="Specific court to book (e.g., 'Pickleball Court 5C (Bubble B)'). If not set, tries all courts.",
        ),
    ] = None,
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
):
    """Book a pickleball court at the specified time."""
    # Set credentials in environment for login module
    if email:
        os.environ["EMAIL"] = email
    if password:
        os.environ["PASSWORD"] = password

    # Wait until specified time if provided
    if wait_until_time:
        try:
            target = parse_wait_time(wait_until_time)
        except ValueError as e:
            raise typer.BadParameter(str(e))
        wait_until(target)

    # Parse the booking date
    booking_date = parse_booking_date(date)
    booking_date_str = booking_date.strftime("%a %m/%d")  # e.g., "Sat 12/14"

    # Parse validated time
    hour, minute = map(int, time_str.split(":"))

    # Check if reservation would exceed closing time
    closing_time = datetime.now().replace(
        hour=FACILITY_CLOSING_HOUR,
        minute=FACILITY_CLOSING_MINUTE,
        second=0,
        microsecond=0
    )
    start_time = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    end_time_full = start_time + timedelta(hours=duration)

    if end_time_full > closing_time:
        # Calculate max available duration
        available_hours = (closing_time - start_time).total_seconds() / 3600
        valid_options = [d for d in VALID_DURATIONS if d <= available_hours]

        if not valid_options:
            raise typer.BadParameter(
                f"Cannot book at {time_str}.\n"
                f"  Facility closes at {FACILITY_CLOSING_HOUR}:00, "
                f"which is less than 1 hour away.\n"
                f"  Please choose an earlier start time."
            )

        max_duration = max(valid_options)
        log.warning(
            f"Requested duration ({duration}h) would exceed closing time "
            f"({FACILITY_CLOSING_HOUR}:00). Adjusting to {max_duration}h."
        )
        duration = max_duration

    page = get_page()

    # Compute target time
    target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    reservation_time = to_12_hour(time_str)

    # Compute actual end time for logging
    actual_end_time = to_12_hour(target + timedelta(hours=duration))

    # Compute end time slot (duration - 30 minutes) for availability checking
    adjusted_duration = timedelta(hours=duration) - timedelta(minutes=30)
    end_time_dt = target + adjusted_duration
    end_time = to_12_hour(end_time_dt)

    # Start the booking process
    log.info("=" * 50)
    log.info("COURT BOOKING STARTED")
    log.info(f"  Date: {booking_date_str}")
    log.info(f"  Time: {reservation_time} - {actual_end_time}")
    log.info(f"  Duration: {duration} hours")
    log.info("=" * 50)

    notify_start("Court Booking", f"**Date:** {booking_date_str}\n**Time:** {reservation_time}\n**Duration:** {duration} hours")

    login()
    select_booking_date(booking_date)

    # Scan for available courts first (only try courts that are actually available)
    if court:
        # User specified a specific court - still scan to verify it's available
        available_courts = get_available_courts(reservation_time, end_time, courts=[court])
        if not available_courts:
            log.error(f"Court {court} is not available at {reservation_time}!")
            notify_failure(f"Court {court} not available for {reservation_time}")
            raise typer.Exit(1)
        courts_to_try = available_courts
        log.info(f"Targeting specific court: {court}")
    else:
        # Scan all courts to find available ones
        available_courts = get_available_courts(reservation_time, end_time)
        if not available_courts:
            log.error("No courts available at the requested time!")
            notify_failure(f"No courts available for {reservation_time}")
            raise typer.Exit(1)
        courts_to_try = available_courts
        log.info(f"Will try {len(available_courts)} available court(s)")

    for court_name in courts_to_try:
        try:
            try:
                check_court_availability(court_name, reservation_time, end_time)
            except Exception as err:
                log.warning(f"  {err}")
                continue  # Try next court

            add_players()
            click_disclosure()
            add_duration(duration)

            click_save_button(target)

            # Check for SweetAlert (error modal)
            alerts = page.query_selector_all(".swal2-modal")

            if alerts:
                log.warning(
                    f"Alert detected after saving on court '{court_name}' - "
                    "assuming conflict, trying next court..."
                )

                # Click the confirm button on the SweetAlert
                confirm_button = page.locator("button.swal2-confirm")
                confirm_button.click()

                close_button = page.locator('button[data-testid="Close"]')
                close_button.click()

                # Continue to next court (DO NOT break)
                continue

            log.info("=" * 50)
            log.info(f"SUCCESS! Reservation saved on court: {court_name}")
            log.info("=" * 50)

            # Send Discord notification
            notify_success(court_name, booking_date_str, reservation_time, duration)

            # If we got here without throwing, we consider it a success and stop
            break

        except Exception as err:
            log.error(f"Unexpected error on court '{court_name}': {err}")
            # Continue to next court
    else:
        # This runs if we didn't break (no court was booked)
        log.error("=" * 50)
        log.error("BOOKING FAILED - No courts available")
        log.error("=" * 50)

        if court:
            # User specified a specific court
            failure_msg = (
                f"Could not book court '{court}' for {reservation_time}.\n"
                f"The court may already be booked or unavailable."
            )
        else:
            failure_msg = (
                f"Could not book any court for {reservation_time}.\n"
                f"All {len(COURTS)} courts were either unavailable or booking failed.\n"
                f"This typically happens when:\n"
                f"  - All courts are already booked at this time\n"
                f"  - The booking window hasn't opened yet\n"
                f"  - There was a conflict with existing reservations"
            )
        notify_failure(failure_msg)
        close_browser()
        sys.exit(1)  # Exit with error code so bot knows it failed

    log.info("Closing browser...")
    close_browser()
    log.info("Done.")


def run():
    """Entry point with error handling."""
    try:
        app()
    except SystemExit:
        # Re-raise SystemExit (from sys.exit) without wrapping
        raise
    except Exception as e:
        error_type = type(e).__name__
        log.error(f"Booking failed ({error_type}): {e}")
        notify_failure(f"Court booking failed.\n{error_type}: {e}")
        close_browser()
        sys.exit(1)


if __name__ == "__main__":
    run()
