import logging
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import get_page, BASE_URL, ORG_ID, SCHEDULE_ID, MAX_DAYS_AHEAD
from utils.exceptions import DateSelectionError

log = logging.getLogger(__name__)


def parse_booking_date(value: str | None) -> datetime:
    """
    Parse a date string for booking.

    Supports formats:
        - None or "latest" - 5 days from now (max advance booking)
        - "today" - today's date
        - "tomorrow" - tomorrow's date
        - "+Nd" (e.g., "+3d") - N days from now
        - "MM/DD" (e.g., "12/15") - specific date (current year)

    Returns:
        datetime: The target booking date.

    Raises:
        DateSelectionError: If date is invalid or more than 5 days ahead.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    max_date = today + timedelta(days=MAX_DAYS_AHEAD)

    if value is None or value.lower() == "latest":
        return max_date

    value = value.strip().lower()

    if value == "today":
        return today

    if value == "tomorrow":
        return today + timedelta(days=1)

    # Handle "+Nd" format (e.g., "+3d")
    if value.startswith("+") and value.endswith("d"):
        try:
            days = int(value[1:-1])
            target = today + timedelta(days=days)
        except ValueError:
            raise DateSelectionError(
                f"Invalid relative date format: '{value}'.\n"
                f"  Expected format: +Nd (e.g., +3d for 3 days from now)"
            )
    else:
        # Try parsing as MM/DD (infer year)
        try:
            parsed = datetime.strptime(value, "%m/%d")
            target = parsed.replace(year=today.year)
            # If date already passed this year, it's invalid (can't book past dates)
        except ValueError:
            raise DateSelectionError(
                f"Invalid date format: '{value}'.\n"
                f"  Supported formats:\n"
                f"    - 'today', 'tomorrow', 'latest'\n"
                f"    - '+Nd' (e.g., +3d for 3 days from now)\n"
                f"    - 'MM/DD' (e.g., 12/15 for December 15)"
            )

    # Validate date is not in the past
    if target < today:
        raise DateSelectionError(
            f"Cannot book for past date: {target.strftime('%Y-%m-%d')}.\n"
            f"  Today is {today.strftime('%Y-%m-%d')}."
        )

    # Validate date is not more than MAX_DAYS_AHEAD
    if target > max_date:
        days_requested = (target - today).days
        raise DateSelectionError(
            f"Cannot book more than {MAX_DAYS_AHEAD} days in advance.\n"
            f"  Requested: {target.strftime('%Y-%m-%d')} ({days_requested} days ahead)\n"
            f"  Maximum: {max_date.strftime('%Y-%m-%d')} ({MAX_DAYS_AHEAD} days ahead)"
        )

    return target


def select_booking_date(date: str | datetime | None = None):
    """
    Navigate to bookings page and select a date.

    Args:
        date: The date to book. Can be:
              - None or "latest" for 5 days ahead (default)
              - "today", "tomorrow"
              - "+Nd" (e.g., "+3d") for N days from now
              - "MM/DD" (e.g., "12/15") for a specific date
              - A datetime object
    """
    # Parse date if it's a string
    if isinstance(date, str) or date is None:
        target_date = parse_booking_date(date)
    else:
        target_date = date

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    days_ahead = (target_date - today).days

    page = get_page()
    log.info("Navigating to bookings page...")
    page.goto(f"{BASE_URL}/Online/Reservations/Bookings/{ORG_ID}?sId={SCHEDULE_ID}")
    page.wait_for_load_state("networkidle")

    # Create friendly label
    if days_ahead == 0:
        day_label = "today"
    elif days_ahead == 1:
        day_label = "tomorrow"
    elif days_ahead == MAX_DAYS_AHEAD:
        day_label = "latest available"
    else:
        day_label = f"{days_ahead} days from now"

    log.info(f"Selecting date: {target_date.strftime('%Y-%m-%d')} ({day_label})")

    # Find and click the date picker
    try:
        date_picker = page.locator('a[data-testid="link-0"]')
        date_picker.wait_for(timeout=15000)
        date_picker.click()
    except Exception as e:
        raise DateSelectionError(
            f"Could not find the date picker on the bookings page.\n"
            f"  The page may not have loaded correctly, or you may not be logged in.\n"
            f"  Original error: {e}"
        )

    page.wait_for_timeout(1000)  # Wait for date picker to open

    # Format: YYYY/M/D (month is 0-indexed in JS, but not in Python)
    # The original JS uses: futureDate.getMonth() which is 0-indexed
    formatted_date = f"{target_date.year}/{target_date.month - 1}/{target_date.day}"

    try:
        date_element = page.locator(f'a[data-value="{formatted_date}"]')
        date_element.wait_for(timeout=10000)
        date_element.click()
    except Exception as e:
        if days_ahead == MAX_DAYS_AHEAD:
            hint = "Bookings typically open 5 days in advance at a specific time."
        else:
            hint = "The date may not be available in the calendar."
        raise DateSelectionError(
            f"Could not find date {target_date.strftime('%Y-%m-%d')} in the calendar.\n"
            f"  {hint}\n"
            f"  Original error: {e}"
        )

    log.info("  Date selected")
