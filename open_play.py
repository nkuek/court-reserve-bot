#!/usr/bin/env python3
"""
Open Play registration script for CourtReserve.

Usage:
    python open_play.py
    python open_play.py --event "Pickleball Open Play - Advanced"
"""

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from selenium.webdriver.common.by import By

from constants import get_driver
from utils.find import find
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

DEFAULT_EVENT = "Pickleball Open Play - Intermediate"


@app.command()
def main(
    event_name: Annotated[
        str,
        typer.Option(
            "--event", "-e",
            help="Name of the Open Play event to register for",
        ),
    ] = DEFAULT_EVENT,
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
):
    """Register for an Open Play event."""
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

    driver = get_driver()

    log.info("=" * 50)
    log.info("OPEN PLAY REGISTRATION STARTED")
    log.info(f"  Date: {booking_date_str}")
    log.info(f"  Event: {event_name}")
    log.info("=" * 50)

    notify_start("Open Play Registration", f"**Date:** {booking_date_str}\n**Event:** {event_name}")

    login()
    select_booking_date(booking_date)

    log.info(f"Looking for {event_name}...")

    try:
        details_link = find(
            (
                By.XPATH,
                f"//span[@data-testid='reservation-name' and contains(., '{event_name}')]"
                "/ancestor::div[contains(@class,'reservation-container')]"
                "//a[@data-testid='event-btn-detail' and normalize-space(.)='Details']",
            )
        )
    except Exception as e:
        raise Exception(
            f"Could not find '{event_name}' event on the selected date.\n"
            f"  The event may not be scheduled for this day, or registration hasn't opened.\n"
            f"  Original error: {e}"
        )

    details_link.click()
    log.info("  Found event, clicked Details")

    log.info("Clicking Register link...")
    try:
        register_link = find((By.LINK_TEXT, "Register"))
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
        finalize_button = find(
            (By.XPATH, "//button[normalize-space(.)='Finalize Registration']")
        )
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
    driver.quit()
    log.info("Done.")


def run():
    """Entry point with error handling."""
    try:
        app()
    except Exception as e:
        error_type = type(e).__name__
        log.error(f"Registration failed ({error_type}): {e}")
        notify_failure(f"Open Play registration failed.\n{error_type}: {e}")
        get_driver().quit()
        raise


if __name__ == "__main__":
    run()
