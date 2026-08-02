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
        - None or "latest" - MAX_DAYS_AHEAD days from now (max advance booking)
        - "today" - today's date
        - "tomorrow" - tomorrow's date
        - "+Nd" (e.g., "+3d") - N days from now
        - "MM/DD" (e.g., "12/15") - specific date (current year)

    Returns:
        datetime: The target booking date.

    Raises:
        DateSelectionError: If date is invalid or more than MAX_DAYS_AHEAD days ahead.
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


# A successful selection has never taken more than 2s across the logged runs
# (n=74, median 1s, p90 2s). Failures instead burn the full click timeout and
# never recover, so retrying from a fresh page is strictly better than waiting.
MAX_DATE_SELECT_ATTEMPTS = 3

# Kendo marks the chosen day cell with one of these, depending on version.
_SELECTED_CELL_CLASSES = ("k-selected", "k-state-selected")


def _open_date_picker(page):
    """Open the calendar popup on the bookings page."""
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
    page.wait_for_selector(".k-calendar", timeout=5000)


def _cell_is_selected(page, formatted_date: str) -> bool:
    """
    Report whether Kendo considers the target day selected.

    Reads the widget's own state rather than the picker's label, whose format
    is not something we can rely on.
    """
    classes = page.evaluate(
        """(value) => {
            const cell = document.querySelector(`a[data-value="${value}"]`);
            if (!cell) return null;
            // Kendo puts the state class on the <td>, not the <a>.
            return (cell.closest('td') || cell).className || "";
        }""",
        formatted_date,
    )
    if classes is None:
        return False
    return any(c in classes for c in _SELECTED_CELL_CLASSES)


def _attempt_date_selection(page, target_date: datetime, formatted_date: str):
    """
    One full attempt: open the picker, find the day cell, click it.

    Raises on any failure so the caller can retry from a freshly loaded page.
    """
    _open_date_picker(page)

    date_element = page.locator(f'a[data-value="{formatted_date}"]')
    date_element.wait_for(timeout=10000)

    try:
        # Short timeout so we fail fast instead of burning the default 30s
        # retrying against an overlay (e.g. the mmenu slideout header logo,
        # class "mm-slideout", which has been observed intercepting pointer
        # events on the calendar date cell).
        date_element.click(timeout=5000)
        return
    except Exception as click_err:
        # Fall back to dispatching the DOM click event directly. This skips
        # Playwright's actionability/hit-test checks, so an overlapping
        # element or a cell that briefly goes not-visible can't block it.
        # Kendo's calendar binds its handler to the cell's click event, so
        # this still triggers date selection.
        log.warning(
            f"Normal click on date cell failed ({click_err.__class__.__name__}); "
            f"falling back to dispatch_event('click')"
        )
        date_element.dispatch_event("click")

    # The intercepting click can also dismiss the calendar, so a closed popup
    # is not proof the date took. Confirm against the widget before returning:
    # proceeding on an unselected date would silently book the wrong day.
    if not _cell_is_selected(page, formatted_date):
        raise DateSelectionError(
            f"dispatch_event('click') did not select "
            f"{target_date.strftime('%Y-%m-%d')} in the calendar widget"
        )
    log.info("  Verified date selection after dispatch_event fallback")


def select_booking_date(date: str | datetime | None = None):
    """
    Navigate to bookings page and select a date.

    Retries the whole open-picker-and-click sequence from a fresh page load,
    because the observed failure mode (an overlay intercepting the click while
    the scheduler's AJAX re-renders) leaves the calendar closed and the cell
    permanently invisible — an in-place retry cannot recover from it.

    Args:
        date: The date to book. Can be:
              - None or "latest" for MAX_DAYS_AHEAD days ahead (default)
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

    # Create friendly label
    if days_ahead == 0:
        day_label = "today"
    elif days_ahead == 1:
        day_label = "tomorrow"
    elif days_ahead == MAX_DAYS_AHEAD:
        day_label = "latest available"
    else:
        day_label = f"{days_ahead} days from now"

    # Format: YYYY/M/D (month is 0-indexed in JS, but not in Python)
    # The original JS uses: futureDate.getMonth() which is 0-indexed
    formatted_date = f"{target_date.year}/{target_date.month - 1}/{target_date.day}"

    page = get_page()
    last_error: Exception | None = None

    for attempt in range(1, MAX_DATE_SELECT_ATTEMPTS + 1):
        log.info("Navigating to bookings page...")
        page.goto(f"{BASE_URL}/Online/Reservations/Bookings/{ORG_ID}?sId={SCHEDULE_ID}")
        # Use domcontentloaded instead of networkidle (faster, we wait for elements anyway)
        page.wait_for_load_state("domcontentloaded")

        log.info(
            f"Selecting date: {target_date.strftime('%Y-%m-%d')} ({day_label}) "
            f"[attempt {attempt}/{MAX_DATE_SELECT_ATTEMPTS}]"
        )

        try:
            _attempt_date_selection(page, target_date, formatted_date)
            log.info("  Date selected")
            return
        except Exception as e:
            last_error = e
            if attempt < MAX_DATE_SELECT_ATTEMPTS:
                log.warning(
                    f"Date selection attempt {attempt} failed "
                    f"({e.__class__.__name__}); reloading and retrying"
                )

    if days_ahead == MAX_DAYS_AHEAD:
        hint = f"Bookings typically open {MAX_DAYS_AHEAD} days in advance at a specific time."
    else:
        hint = "The date may not be available in the calendar."
    raise DateSelectionError(
        f"Could not find date {target_date.strftime('%Y-%m-%d')} in the calendar "
        f"after {MAX_DATE_SELECT_ATTEMPTS} attempts.\n"
        f"  {hint}\n"
        f"  Last error: {last_error}"
    )
