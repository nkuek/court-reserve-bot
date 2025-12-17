#!/usr/bin/env python3
"""
Open Play registration script for CourtReserve.

Usage:
    python open_play.py
    python open_play.py --date tomorrow
"""

import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from constants import get_page, close_browser
from utils.login import login
from utils.booking_date import select_booking_date, parse_booking_date
from utils.discord import notify_open_play_success, notify_failure, notify_start
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

app = typer.Typer(help="Register for Open Play events on CourtReserve.")


@app.command()
def main(
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
    """Register for an Open Play event."""
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

    # Open play events are only on Tuesdays (1) and Thursdays (3)
    day_of_week = booking_date.weekday()
    if day_of_week not in (1, 3):  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        day_name = booking_date.strftime("%A")
        raise typer.BadParameter(
            f"{booking_date_str} is a {day_name}.\n"
            f"  Open Play events are only on Tuesdays and Thursdays.\n"
            f"  Please choose a different date."
        )

    page = get_page()

    log.info("=" * 50)
    log.info("OPEN PLAY REGISTRATION STARTED")
    log.info(f"  Date: {booking_date_str}")
    log.info("=" * 50)

    notify_start("Open Play Registration", f"**Date:** {booking_date_str}")

    login()
    select_booking_date(booking_date)

    log.info("Looking for Pickleball Open Play event...")

    # Find any Pickleball Open Play event on this date
    try:
        # First, find the event name element to log what we found
        event_name_locator = page.locator(
            "span[data-testid='reservation-name']:has-text('Pickleball Open Play')"
        )
        event_name_locator.first.wait_for(timeout=10000)
        event_name = (event_name_locator.first.text_content() or "").strip()
        log.info(f"  Found event: {event_name}")

        # Now find the Details link for this event
        details_link = page.locator(
            "span[data-testid='reservation-name']:has-text('Pickleball Open Play')"
        ).locator("xpath=ancestor::div[contains(@class,'reservation-container')]").locator(
            "a[data-testid='event-btn-detail']:has-text('Details')"
        ).first
        details_link.wait_for(timeout=10000)
    except Exception as e:
        raise Exception(
            f"Could not find any Pickleball Open Play event on {booking_date_str}.\n"
            f"  Intermediate events are typically on Tuesdays, Thursday events on Thursdays.\n"
            f"  The event may not be scheduled for this day, or registration hasn't opened.\n"
            f"  Original error: {e}"
        )

    details_link.click()
    log.info("  Clicked Details")

    log.info("Clicking Register link...")
    try:
        register_link = page.locator("a:has-text('Register')").first
        register_link.wait_for(timeout=10000)
    except Exception as e:
        raise Exception(
            f"Could not find 'Register' link for '{event_name}'.\n"
            f"  You may already be registered, or registration is not open.\n"
            f"  Original error: {e}"
        )

    register_link.click()
    log.info("  Clicked Register")

    log.info("Finalizing registration...")
    try:
        finalize_button = page.locator("button:has-text('Finalize Registration')")
        finalize_button.wait_for(timeout=10000)
    except Exception as e:
        raise Exception(
            f"Could not find 'Finalize Registration' button.\n"
            f"  The registration form may not have loaded correctly.\n"
            f"  Original error: {e}"
        )

    finalize_button.click()

    log.info("=" * 50)
    log.info("SUCCESS! Registration completed")
    log.info("=" * 50)

    # Send Discord notification
    notify_open_play_success(event_name, booking_date_str)

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
        log.error(f"Registration failed ({error_type}): {e}")
        notify_failure(f"Open Play registration failed.\n{error_type}: {e}")
        close_browser()
        sys.exit(1)


if __name__ == "__main__":
    run()
