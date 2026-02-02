#!/usr/bin/env python3
"""
Court booking script for CourtReserve.

Usage:
    python court_booking.py --time 21:00 --duration 2
    python court_booking.py --time 21:00 --duration 2 --parallel  # Try all courts simultaneously
"""

import logging
import multiprocessing
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
    """Convert duration hours to dropdown index (0-indexed)."""
    return VALID_DURATIONS.index(hours)


def add_players():
    """Add 3 placeholder players to the reservation."""
    log.info("Adding placeholder players...")
    page = get_page()

    for i in range(3):
        additional_players_input = page.locator("[name='OwnersDropdown_input']")
        additional_players_input.wait_for(timeout=10000)
        additional_players_input.fill("Placeholder")

        # Wait for dropdown results to appear (much faster than fixed 5s wait)
        page.locator("#OwnersDropdown_listbox li").first.wait_for(timeout=10000)
        # Small delay for the UI to be ready for input
        page.wait_for_timeout(300)

        additional_players_input.press("Enter")

        # Wait for the player to be added (tag appears in the form)
        # This replaces the old 5-second wait with a condition-based wait
        page.wait_for_timeout(200)  # Brief delay for form state update
        log.info(f"  Added placeholder {i + 1}/3")

    log.info("Successfully added all placeholders")


def click_disclosure():
    """Click the disclosure agreement checkbox."""
    log.info("Clicking disclosure checkbox...")
    page = get_page()
    disclosure_label = page.locator("label[for='DisclosureAgree']")
    disclosure_label.wait_for(timeout=3000)
    disclosure_label.click()
    log.info("Disclosure accepted")


def add_duration(duration_hours: float):
    """Set the reservation duration (optimized for speed)."""
    log.info(f"Setting duration to {duration_hours} hours...")
    page = get_page()

    duration_input = page.locator("span[aria-owns='Duration_listbox']")
    duration_input.wait_for(timeout=3000)
    duration_input.click()

    # Wait for dropdown to open - shorter timeout
    dropdown_list = page.locator('ul[data-testid="Duration-container"][aria-hidden="false"]')
    dropdown_list.wait_for(timeout=2000)

    # Click directly on the duration item instead of using slow arrow key navigation
    duration_index = duration_to_index(duration_hours)
    duration_item = dropdown_list.locator("li").nth(duration_index)
    duration_item.click()

    log.info(f"Duration set to {duration_hours} hours")


def _save_debug_snapshot(suffix: str):
    """Save screenshot and HTML for debugging (only in headless mode or if DEBUG_SNAPSHOTS is set)."""
    # Skip if tracing is enabled - traces already capture screenshots and DOM
    if os.environ.get("ENABLE_TRACING", "false").lower() == "true":
        return None, None

    if not (os.environ.get("HEADLESS", "false").lower() == "true" or os.environ.get("DEBUG_SNAPSHOTS")):
        return None, None

    page = get_page()
    debug_dir = Path(__file__).parent / "data" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save screenshot
    screenshot_path = debug_dir / f"{timestamp}_{suffix}.png"
    try:
        page.screenshot(path=screenshot_path)
        log.info(f"  Screenshot saved: {screenshot_path}")
    except Exception as e:
        log.warning(f"  Failed to save screenshot: {e}")
        screenshot_path = None

    # Save HTML
    html_path = debug_dir / f"{timestamp}_{suffix}.html"
    try:
        html_content = page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        log.info(f"  HTML saved: {html_path}")
    except Exception as e:
        log.warning(f"  Failed to save HTML: {e}")
        html_path = None

    # Clean up old debug files (keep last 20 of each type)
    for ext in ["*.png", "*.html"]:
        files = sorted(debug_dir.glob(ext), key=os.path.getmtime)
        for old_file in files[:-20]:
            try:
                os.remove(old_file)
            except Exception:
                pass

    return screenshot_path, html_path


def click_save_button(target_time: datetime) -> bool:
    """Wait until target time and click the save button with high precision.

    Returns:
        True if the booking appears successful (modal closed), False otherwise.
    """
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

        # Calculate the actual click time (2 seconds before target to beat server queue)
        early_click_time = target_time - timedelta(seconds=2)

        # Log countdown every 30 seconds (or every second if < 30s remaining)
        last_log_time = None
        while True:
            now = datetime.now()
            remaining = (early_click_time - now).total_seconds()

            # Break when we're within 50ms of click time (switch to busy-wait)
            if remaining <= 0.05:
                break

            remaining_to_target = (target_time - now).total_seconds()
            hours = int(remaining_to_target // 3600)
            minutes = int((remaining_to_target % 3600) // 60)
            seconds = int(remaining_to_target % 60)

            # Log every 30 seconds, or every second in final 10 seconds
            current_remaining_int = int(remaining_to_target)
            should_log = (
                last_log_time is None or
                remaining_to_target <= 10 or
                current_remaining_int % 30 == 0 and current_remaining_int != last_log_time
            )

            if should_log:
                log.info(f"  {hours:02d}:{minutes:02d}:{seconds:02d} remaining...")
                last_log_time = current_remaining_int

            # Sleep for ~1 second, but check more frequently near the end
            sleep_time = min(1.0, remaining - 0.05)
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Final busy-wait (spin loop) for precise timing
        while datetime.now() < early_click_time:
            pass

    # Use JavaScript click for faster execution
    save_button.evaluate("el => el.click()")

    click_time = datetime.now()
    diff_from_target_ms = (click_time - target_time).total_seconds() * 1000
    log.info(f"CLICKED Save button at {click_time.strftime('%H:%M:%S.%f')[:-3]} ({diff_from_target_ms:+.1f}ms from target, intended -2000ms)")

    # Wait for the booking result - either modal closes (success) or error alert appears
    log.info("Waiting for booking result...")
    error_alert = page.locator('.swal2-modal')
    # Check for the booking form title - if it's gone, modal closed (success)
    booking_form_title = page.locator('span[data-testid="title"]:has-text("Book a reservation")')
    # Also use data-testid for the save button since text changes during loading
    save_btn = page.locator('button[data-testid="Save"], button[data-testid="save-btn"]')

    # Poll for up to 30 seconds for a definitive result (reduced from 60s)
    max_wait_seconds = 30
    poll_interval_ms = 150  # Faster polling for quicker detection
    wait_start = time.time()

    while time.time() - wait_start < max_wait_seconds:
        # Check if error alert appeared
        if error_alert.count() > 0:
            log.warning("Error alert detected - booking failed")
            _save_debug_snapshot("after_save")
            return False

        # Check if booking form title is gone (modal closed = success)
        try:
            if booking_form_title.count() == 0 or not booking_form_title.is_visible():
                log.info("Booking modal closed - booking appears successful")
                _save_debug_snapshot("after_save")
                return True
        except Exception:
            # Element detached = modal closed
            log.info("Booking modal closed - booking appears successful")
            _save_debug_snapshot("after_save")
            return True

        # Check if save button is no longer disabled (form submitted successfully)
        try:
            button_visible = save_btn.count() > 0 and save_btn.is_visible()
            button_disabled = button_visible and save_btn.is_disabled()

            if button_visible and not button_disabled:
                # Button is enabled again - submission completed, check result
                page.wait_for_timeout(100)
                if error_alert.count() > 0:
                    log.warning("Error alert appeared after submission")
                    _save_debug_snapshot("after_save")
                    return False
                # Check if modal closed
                if booking_form_title.count() == 0 or not booking_form_title.is_visible():
                    log.info("Save button reset and modal closed - booking successful")
                    _save_debug_snapshot("after_save")
                    return True
        except Exception:
            # Button might be detached if modal closed
            pass

        page.wait_for_timeout(poll_interval_ms)

    # Timeout - couldn't determine result
    log.warning(f"Timeout after {max_wait_seconds}s waiting for booking result")
    _save_debug_snapshot("after_save")

    # Final check
    if error_alert.count() > 0:
        return False

    # If modal closed during our final check, consider it success
    try:
        if booking_form_title.count() == 0 or not booking_form_title.is_visible():
            return True
    except Exception:
        return True

    # Still on booking form after timeout = likely failed
    log.warning("Still on booking form after timeout - assuming failure")
    return False


def check_court_availability(court: str, reservation_time: str, end_time: str):
    """Check if a court is available at the specified time."""
    log.info(f"Checking court: {court} for {reservation_time}")
    page = get_page()

    # Scroll scheduler viewport to ensure time slots below the fold are rendered
    try:
        scheduler = page.locator(".k-scheduler-content")
        scheduler.wait_for(timeout=5000)
        for frac in (0.0, 0.5, 1.0):
            scheduler.evaluate("el => { el.scrollTop = el.scrollHeight * %s; }" % frac)
            page.wait_for_timeout(150)
    except Exception as e:
        log.debug(f"  Scheduler scroll skipped: {e}")

    # Use exact text match with 'Reserve X:XX PM' to avoid partial matches
    # e.g., '2:00 PM' would otherwise match '12:00 PM'
    try:
        start_time_btn = page.locator(
            f"button[data-courtlabel='{court}']:text('Reserve {reservation_time}')"
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
            f"button[data-courtlabel='{court}']:text('Reserve {end_time}')"
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


def _parallel_book_court(
    court_name: str,
    booking_date: datetime,
    reservation_time: str,
    end_time: str,
    duration: float,
    target_time: datetime,
    result_queue: multiprocessing.Queue,
    click_offset_ms: int = 0,
    worker_id: str = "",
):
    """
    Worker function for parallel court booking.
    Runs in its own process with its own browser instance.

    Args:
        click_offset_ms: Offset from target_time to click (negative = before target)
        worker_id: Identifier for this worker (for logging)
    """
    # Calculate actual click time with offset
    actual_click_time = target_time + timedelta(milliseconds=click_offset_ms)

    # Re-configure logging for this process with flush to ensure output is visible
    label = worker_id or court_name[-2:]

    # Create handler that flushes immediately
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(f'%(asctime)s [{label}] %(message)s', datefmt='%H:%M:%S'))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True
    )
    worker_log = logging.getLogger(__name__)

    # Force unbuffered output for this process
    sys.stdout.reconfigure(line_buffering=True)

    try:
        worker_log.info(f"Starting worker for {court_name} (click offset: {click_offset_ms:+d}ms)")

        # Each process needs its own browser
        login()
        select_booking_date(booking_date)

        # Open the booking form for this court
        try:
            check_court_availability(court_name, reservation_time, end_time)
        except Exception as err:
            worker_log.error(f"Court not available: {err}")
            result_queue.put({"court": court_name, "worker_id": worker_id, "success": False, "error": str(err)})
            return

        # Fill the form
        add_players()
        click_disclosure()
        add_duration(duration)

        worker_log.info(f"Form filled, will click at {actual_click_time.strftime('%H:%M:%S.%f')[:-3]}")

        # Wait and click Save (using custom click time, not the default -2s early)
        booking_succeeded = _click_save_at_time(actual_click_time)

        if booking_succeeded:
            worker_log.info("SUCCESS!")
            result_queue.put({"court": court_name, "worker_id": worker_id, "success": True})
        else:
            worker_log.warning("Booking failed")
            result_queue.put({"court": court_name, "worker_id": worker_id, "success": False, "error": "Booking failed"})

    except Exception as e:
        worker_log.error(f"Worker error: {e}")
        result_queue.put({"court": court_name, "worker_id": worker_id, "success": False, "error": str(e)})
    finally:
        try:
            close_browser()
        except:
            pass


def _click_save_at_time(click_time: datetime) -> bool:
    """Click the save button at a specific time (for staggered parallel attempts)."""
    page = get_page()
    save_button = page.locator('button[data-testid="Save"]')
    save_button.wait_for(timeout=10000)

    # Wait until click time
    now = datetime.now()
    if click_time > now:
        # Sleep until close to click time
        sleep_duration = (click_time - now).total_seconds() - 0.05
        if sleep_duration > 0:
            time.sleep(sleep_duration)
        # Busy-wait for final precision
        while datetime.now() < click_time:
            pass

    # Click
    save_button.evaluate("el => el.click()")
    actual_click = datetime.now()
    diff_ms = (actual_click - click_time).total_seconds() * 1000
    log = logging.getLogger(__name__)
    log.info(f"CLICKED at {actual_click.strftime('%H:%M:%S.%f')[:-3]} (diff: {diff_ms:+.1f}ms)")

    # Wait for result (same logic as click_save_button)
    error_alert = page.locator('.swal2-modal')
    booking_form_title = page.locator('span[data-testid="title"]:has-text("Book a reservation")')

    max_wait_seconds = 30
    poll_interval_ms = 150  # Faster polling
    start_time = time.time()

    while time.time() - start_time < max_wait_seconds:
        if error_alert.count() > 0:
            return False
        try:
            if booking_form_title.count() == 0 or not booking_form_title.is_visible():
                return True
        except:
            pass
        page.wait_for_timeout(poll_interval_ms)

    return False


def _run_parallel_booking(
    courts: list[str],
    booking_date: datetime,
    reservation_time: str,
    end_time: str,
    duration: float,
    target_time: datetime,
    booking_date_str: str,
    attempts_per_court: int = 1,
) -> bool:
    """
    Run parallel booking attempts for multiple courts.

    Args:
        courts: List of court names to try
        attempts_per_court: Number of parallel attempts per court with staggered timing
                           e.g., 4 attempts = clicks at -2s, -1.5s, -1s, -0.5s

    Returns True if any court was successfully booked.
    """
    total_processes = len(courts) * attempts_per_court

    log.info("=" * 50)
    log.info(f"PARALLEL BOOKING MODE")
    log.info(f"  Courts: {len(courts)}")
    log.info(f"  Attempts per court: {attempts_per_court}")
    log.info(f"  Total processes: {total_processes}")
    log.info("=" * 50)

    # Fixed stagger: -750ms, -500ms, -250ms, 0ms (min 4 attempts per court). Extra attempts also at 0ms.
    if attempts_per_court <= 1:
        offsets_ms = [0]
    else:
        base_offsets = [-750, -500, -250, 0]
        if attempts_per_court <= 4:
            offsets_ms = base_offsets[:attempts_per_court]
        else:
            offsets_ms = base_offsets + [0] * (attempts_per_court - 4)

    log.info(f"  Click offsets: {offsets_ms} ms")

    result_queue = multiprocessing.Queue()
    processes = []

    # Spawn processes for each court × attempt combination
    for court_name in courts:
        for attempt_idx, offset_ms in enumerate(offsets_ms):
            worker_id = f"{court_name[-2:]}-{attempt_idx+1}"
            p = multiprocessing.Process(
                target=_parallel_book_court,
                args=(court_name, booking_date, reservation_time, end_time, duration, target_time, result_queue),
                kwargs={"click_offset_ms": offset_ms, "worker_id": worker_id},
            )
            processes.append((worker_id, p))
            p.start()
            log.info(f"Started {worker_id} (offset: {offset_ms:+d}ms)")

    # Wait for all processes to complete (with timeout)
    timeout = 120  # 2 minutes max
    for worker_id, p in processes:
        p.join(timeout=timeout)
        if p.is_alive():
            log.warning(f"Process {worker_id} timed out, terminating...")
            p.terminate()
            p.join(timeout=5)

    # Collect results
    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    # Check for success
    successes = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]

    log.info("")
    log.info("=" * 50)
    log.info("PARALLEL BOOKING RESULTS")
    log.info("=" * 50)

    for r in successes:
        worker = r.get('worker_id', '')
        log.info(f"  ✓ {r['court']} ({worker}): SUCCESS")
    for r in failures:
        worker = r.get('worker_id', '')
        log.info(f"  ✗ {r['court']} ({worker}): {r.get('error', 'Failed')}")

    if successes:
        # Notify about the first successful booking
        first_success = successes[0]
        log.info(f"\nBooked court: {first_success['court']}")
        notify_success(first_success['court'], booking_date_str, reservation_time, duration)
        return True
    else:
        log.error("\nAll parallel booking attempts failed!")
        notify_failure(f"Parallel booking failed for {reservation_time} - all {total_processes} attempts failed")
        return False


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
            help="Wait until this time before starting. Formats: 07:00, tomorrow 07:00, +1d 07:00, 2025-12-14 07:00. Default: 1 minute before reservation time.",
        ),
    ] = None,
    no_wait: Annotated[
        bool,
        typer.Option(
            "--no-wait",
            help="Skip waiting and execute immediately (useful for testing)",
        ),
    ] = False,
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
    parallel: Annotated[
        bool,
        typer.Option(
            "--parallel", "-p",
            help="Try all available courts simultaneously using separate browser instances. Much faster but uses more resources.",
        ),
    ] = False,
    attempts: Annotated[
        int,
        typer.Option(
            "--attempts", "-a",
            help="Number of parallel attempts per court with staggered timing (requires --parallel). e.g., 4 = clicks at -2s, -1.5s, -1s, -0.5s",
        ),
    ] = 3,
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

    # Parse the booking date and time first (needed for default wait calculation)
    booking_date = parse_booking_date(date)
    booking_date_str = booking_date.strftime("%a %m/%d")  # e.g., "Sat 12/14"
    hour, minute = map(int, time_str.split(":"))

    # Calculate the target click time (reservation time on today's date for the countdown)
    target_click_time = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Parse wait time if provided (validate early, but wait later after login)
    wait_target = None
    if not no_wait:
        if wait_until_time:
            try:
                wait_target = parse_wait_time(wait_until_time)
            except ValueError as e:
                raise typer.BadParameter(str(e))
        else:
            # Default: wait until 1 minute before target time
            default_wait_target = target_click_time - timedelta(minutes=1)
            if default_wait_target > datetime.now():
                wait_target = default_wait_target

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

    # Use the target click time computed earlier
    target = target_click_time
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

    # Login first, then wait - so we're ready to go when the time comes
    login()
    select_booking_date(booking_date)

    # Now wait for the target time (after login so we're ready)
    if no_wait:
        log.info("--no-wait specified, executing immediately")
    elif wait_target:
        if wait_until_time:
            log.info(f"Waiting until {wait_target.strftime('%H:%M:%S')} before scanning courts...")
        else:
            log.info(f"Default behavior: waiting until 1 minute before {target_click_time.strftime('%H:%M:%S')}")
        wait_until(wait_target)
    else:
        log.info("Target time is less than 1 minute away, proceeding immediately")

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

    # PARALLEL MODE: Try all courts simultaneously
    # Activate if --parallel flag set AND (multiple courts OR multiple attempts per court)
    log.info(f"Parallel mode check: parallel={parallel}, courts={len(courts_to_try)}, attempts={attempts}")
    if parallel and (len(courts_to_try) > 1 or attempts > 1):
        # Ensure at least 4 processes per court
        attempts_per_court = max(4, attempts)

        # Strategy:
        # - If multiple courts are available: spread attempts across all courts with min 2 per court
        # - If only one court: stack attempts on that court (use --attempts, min 2)
        if len(courts_to_try) > 1:
            log.info(f"Multiple courts available ({len(courts_to_try)}). Spreading processes: {attempts_per_court} attempt(s) per court (min 2).")
        else:
            log.info(f"Single court available. Using {attempts_per_court} attempt(s) on the same court (min 2).")

        # Close the current browser - parallel processes will create their own
        close_browser()

        success = _run_parallel_booking(
            courts=courts_to_try,
            booking_date=booking_date,
            reservation_time=reservation_time,
            end_time=end_time,
            duration=duration,
            target_time=target,
            booking_date_str=booking_date_str,
            attempts_per_court=attempts_per_court,
        )

        if success:
            return
        else:
            raise typer.Exit(1)

    # SEQUENTIAL MODE: Try courts one by one
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

            booking_succeeded = click_save_button(target)

            # Check for SweetAlert (error modal) - may have appeared during wait
            alerts = page.query_selector_all(".swal2-modal")

            if alerts or not booking_succeeded:
                log.warning(
                    f"Booking failed on court '{court_name}' - "
                    "trying next court..."
                )

                # Save debug snapshot of the error state
                _save_debug_snapshot(f"error_alert_{court_name.replace(' ', '_')}")

                if alerts:
                    # Click the confirm button on the SweetAlert
                    confirm_button = page.locator("button.swal2-confirm")
                    if confirm_button.count() > 0:
                        confirm_button.click()

                    close_button = page.locator('button[data-testid="Close"]')
                    if close_button.count() > 0:
                        close_button.click()

                # Continue to next court (DO NOT break)
                continue

            # Save success snapshot
            _save_debug_snapshot(f"success_{court_name.replace(' ', '_')}")

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

        # Save final state for debugging
        _save_debug_snapshot("booking_failed_all_courts")

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

        # Try to save debug snapshot before closing
        try:
            _save_debug_snapshot(f"exception_{error_type}")
        except Exception:
            pass

        notify_failure(f"Court booking failed.\n{error_type}: {e}")
        close_browser()
        sys.exit(1)


if __name__ == "__main__":
    run()
