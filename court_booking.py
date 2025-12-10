#!/usr/bin/env python3
"""
Court booking script for CourtReserve.

Usage:
    python court_booking.py --time=21:00 --duration=2
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from constants import driver, BASE_URL
from utils.find import find
from utils.login import login
from utils.click_latest_available_date import click_latest_available_date


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


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Book a court on CourtReserve")
    parser.add_argument(
        "--time",
        required=True,
        help="Reservation time in 24h format (e.g., 21:00 for 9:00 PM)",
    )
    parser.add_argument(
        "--duration",
        required=True,
        type=float,
        help="Duration in hours (1, 1.5, 2, 2.5, or 3)",
    )
    return parser.parse_args()


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
    options = [1, 1.5, 2, 2.5, 3]
    try:
        # 1 hour is selected by default, so subtract 1
        index = options.index(hours) - 1
        if index == -1:
            return -1  # 1 hour is already selected
        return index
    except ValueError:
        raise ValueError(f"Unsupported duration: {hours}. Must be 1, 1.5, 2, 2.5, or 3")


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

        # Verify the time slot is available by checking the end time
        find(
            (
                By.XPATH,
                f"//button[@data-courtlabel='{court}' and contains(text(), '{end_time}')]",
            )
        )
        log.info(f"  Found end time {end_time} - slot available!")

        start_time_btn.click()

    except Exception:
        raise Exception(
            f'Time slot for court "{court}" at {reservation_time} not found. '
            "It may be already booked."
        )


def main():
    args = parse_args()

    # Parse time
    try:
        hour, minute = map(int, args.time.split(":"))
    except ValueError:
        raise ValueError(f"Invalid --time format: {args.time}")

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid --time value: {args.time}")

    duration_hours = args.duration
    if duration_hours <= 0:
        raise ValueError(f"Invalid --duration value: {duration_hours}")

    # Compute target time
    target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    reservation_time = to_12_hour(args.time)

    # Compute end time (duration - 30 minutes)
    duration_ms = duration_hours * 60 * 60 * 1000
    adjusted_duration_ms = duration_ms - 30 * 60 * 1000

    if adjusted_duration_ms < 0:
        raise ValueError("Duration must be at least 0.5 hours.")

    from datetime import timedelta
    end_time_dt = target + timedelta(milliseconds=adjusted_duration_ms)
    end_time = to_12_hour(end_time_dt)

    # Start the booking process
    log.info("=" * 50)
    log.info("COURT BOOKING STARTED")
    log.info(f"  Target time: {reservation_time}")
    log.info(f"  End time: {end_time}")
    log.info(f"  Duration: {duration_hours} hours")
    log.info("=" * 50)

    login()
    click_latest_available_date()

    for court in COURTS:
        try:
            try:
                check_court_availability(court, reservation_time, end_time)
            except Exception as err:
                log.warning(f"  {err}")
                continue  # Try next court

            add_players()
            click_disclosure()
            add_duration(duration_hours)

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

            # If we got here without throwing, we consider it a success and stop
            break

        except Exception as err:
            log.error(f"Failed on court '{court}': {err}")
            # Continue to next court

    log.info("Closing browser...")
    driver.quit()
    log.info("Done.")


if __name__ == "__main__":
    main()
