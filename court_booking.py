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
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from constants import get_driver, BASE_URL
from utils.find import find
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

# Valid duration options
VALID_DURATIONS = [1.0, 1.5, 2.0, 2.5, 3.0]

# Facility closes at 23:00
FACILITY_CLOSING_HOUR = 23
FACILITY_CLOSING_MINUTE = 0


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


# Court priority list
COURTS = [
    "Pickleball Court 5C (Bubble B)",
    "Pickleball Court 5B (Bubble B)",
    "Pickleball Court 5A (Bubble B)",
    "Pickleball Court 6A (Bubble B)",
    "Pickleball Court 6B (Bubble B)",
    "Pickleball Court 6C (Bubble B)",
    "Pickleball Court #7A (Bubble B)",
    "Pickleball Court #7B (Bubble B)",
    "Pickleball Court #8A (Bubble B)",
    "Pickleball Court #8B (Bubble B)",
]


def add_players():
    """Add 3 placeholder players to the reservation."""
    log.info("Adding placeholder players...")

    for i in range(3):
        additional_players_input = find((By.NAME, "OwnersDropdown_input"))
        additional_players_input.send_keys("Placeholder")

        # Wait for results to appear
        find((By.CSS_SELECTOR, "#OwnersDropdown_listbox li"), timeout=5)
        time.sleep(1)

        # Highlight first option and select it via keyboard (kendo-friendly)
        additional_players_input.send_keys(Keys.ARROW_DOWN)
        additional_players_input.send_keys(Keys.ENTER)
        log.info(f"  Added placeholder {i + 1}/3")

    log.info("Successfully added all placeholders")


def click_disclosure():
    """Click the disclosure agreement checkbox."""
    log.info("Clicking disclosure checkbox...")
    disclosure_label = find((By.CSS_SELECTOR, "label[for='DisclosureAgree']"))
    disclosure_label.click()
    log.info("Disclosure accepted")
    time.sleep(1)


def add_duration(duration_hours: float):
    """Set the reservation duration."""
    log.info(f"Setting duration to {duration_hours} hours...")

    duration_input = find((By.CSS_SELECTOR, "span[aria-owns='Duration_listbox']"))
    duration_input.click()

    # Wait for dropdown to open
    find(
        (By.CSS_SELECTOR, 'ul[data-testid="Duration-container"][aria-hidden="false"]'),
        timeout=5,
    )

    duration_index = duration_to_index(duration_hours)

    for _ in range(duration_index + 1):
        duration_input.send_keys(Keys.ARROW_DOWN)

    duration_input.send_keys(Keys.ENTER)
    log.info(f"Duration set to {duration_hours} hours")


def click_save_button(target_time: datetime):
    """Wait until target time and click the save button with high precision."""
    driver = get_driver()
    log.info("Preparing to click Save button...")
    save_button = find((By.CSS_SELECTOR, 'button[data-testid="Save"]'))

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

    # Use JavaScript click for faster execution (bypasses Selenium overhead)
    driver.execute_script("arguments[0].click();", save_button)

    click_time = datetime.now()
    diff_ms = (click_time - target_time).total_seconds() * 1000
    log.info(f"CLICKED Save button at {click_time.strftime('%H:%M:%S.%f')[:-3]} (diff: {diff_ms:+.1f}ms)")
    time.sleep(1)


def check_court_availability(court: str, reservation_time: str, end_time: str):
    """Check if a court is available at the specified time."""
    log.info(f"Checking court: {court} for {reservation_time}")

    try:
        start_time_btn = find(
            (
                By.XPATH,
                f"//button[@data-courtlabel='{court}' and contains(text(), '{reservation_time}')]",
            )
        )
        log.info(f"  Found start time {reservation_time}")
    except Exception:
        raise CourtUnavailableError(
            f"Start time {reservation_time} not available on {court}.\n"
            f"  The time slot may already be booked."
        )

    try:
        # Verify the time slot is available by checking the end time
        find(
            (
                By.XPATH,
                f"//button[@data-courtlabel='{court}' and contains(text(), '{end_time}')]",
            )
        )
        log.info(f"  Found end time {end_time} - slot available!")
    except Exception:
        raise CourtUnavailableError(
            f"Full duration not available on {court}.\n"
            f"  Start time {reservation_time} found, but end time {end_time} is blocked.\n"
            f"  Another reservation may overlap with your requested time slot."
        )

    start_time_btn.click()


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

    driver = get_driver()

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

    for court in COURTS:
        try:
            try:
                check_court_availability(court, reservation_time, end_time)
            except Exception as err:
                log.warning(f"  {err}")
                continue  # Try next court

            add_players()
            click_disclosure()
            add_duration(duration)

            click_save_button(target)

            # Check for SweetAlert (error modal)
            alerts = driver.find_elements(By.CLASS_NAME, "swal2-modal")

            if alerts:
                log.warning(
                    f"Alert detected after saving on court '{court}' - "
                    "assuming conflict, trying next court..."
                )

                # Click the confirm button on the SweetAlert
                confirm_button = find((By.CSS_SELECTOR, "button.swal2-confirm"))
                confirm_button.click()

                close_button = find((By.CSS_SELECTOR, 'button[data-testid="Close"]'))
                close_button.click()

                # Continue to next court (DO NOT break)
                continue

            log.info("=" * 50)
            log.info(f"SUCCESS! Reservation saved on court: {court}")
            log.info("=" * 50)

            # Send Discord notification
            notify_success(court, booking_date_str, reservation_time, duration)

            # If we got here without throwing, we consider it a success and stop
            break

        except Exception as err:
            log.error(f"Unexpected error on court '{court}': {err}")
            # Continue to next court
    else:
        # This runs if we didn't break (no court was booked)
        notify_failure(
            f"Could not book any court for {reservation_time}.\n"
            f"All {len(COURTS)} courts were either unavailable or booking failed.\n"
            f"This typically happens when:\n"
            f"  - All courts are already booked at this time\n"
            f"  - The booking window hasn't opened yet\n"
            f"  - There was a conflict with existing reservations"
        )

    log.info("Closing browser...")
    driver.quit()
    log.info("Done.")


if __name__ == "__main__":
    app()
