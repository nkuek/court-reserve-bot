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
import time
from datetime import datetime, timedelta
from multiprocessing import Process, Queue
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
    screenshot_path = debug_dir / f"openplay_{timestamp}_{suffix}.png"
    try:
        page.screenshot(path=screenshot_path)
        log.info(f"  Screenshot saved: {screenshot_path}")
    except Exception as e:
        log.warning(f"  Failed to save screenshot: {e}")
        screenshot_path = None

    # Save HTML
    html_path = debug_dir / f"openplay_{timestamp}_{suffix}.html"
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
        files = sorted(debug_dir.glob(f"openplay_*{ext}"), key=os.path.getmtime)
        for old_file in files[:-20]:
            try:
                os.remove(old_file)
            except Exception:
                pass

    return screenshot_path, html_path


def _wait_for_registration_result(page, max_wait_seconds: int = 60) -> bool:
    """Wait for the registration to complete and determine success/failure.

    Args:
        page: Playwright page object
        max_wait_seconds: Maximum time to wait for a result

    Returns:
        True if registration appears successful, False otherwise.
    """
    error_alert = page.locator('.swal2-modal')
    # Use data-testid since button text changes during loading
    save_button = page.locator('button[data-testid="save-btn"]')
    spinner = page.locator('button[data-testid="save-btn"] .btn-active-spinner')
    # Check for "Register to Program" heading which indicates we're still on the form
    register_heading = page.locator('text="Register to Program"')

    poll_interval_ms = 500
    start_time = time.time()

    while time.time() - start_time < max_wait_seconds:
        # Check if error alert appeared
        if error_alert.count() > 0:
            log.warning("Error alert detected - registration failed")
            return False

        # Check if we navigated away from the registration page (success)
        # If the "Register to Program" heading is gone, we likely succeeded
        try:
            if register_heading.count() == 0 or not register_heading.is_visible():
                log.info("Navigated away from registration page - registration successful")
                return True
        except Exception:
            # Element detached = page navigated
            log.info("Page navigated - registration appears successful")
            return True

        # Check if button still has spinner (still loading)
        try:
            button_exists = save_button.count() > 0 and save_button.is_visible()
            button_disabled = button_exists and save_button.is_disabled()

            if button_exists and not button_disabled:
                # Button is enabled again - submission completed, check result
                page.wait_for_timeout(500)
                if error_alert.count() > 0:
                    log.warning("Error alert appeared after submission")
                    return False
                if register_heading.count() == 0 or not register_heading.is_visible():
                    log.info("Form submitted and page navigated - registration successful")
                    return True
                # Still on form but button enabled - might have failed silently
                log.warning("Button re-enabled but still on registration page")
        except Exception:
            pass

        page.wait_for_timeout(poll_interval_ms)

    # Timeout - couldn't determine result
    log.warning(f"Timeout after {max_wait_seconds}s waiting for registration result")

    # Final check
    if error_alert.count() > 0:
        return False

    # If we're no longer on the registration page, consider it success
    try:
        if register_heading.count() == 0 or not register_heading.is_visible():
            return True
    except Exception:
        return True

    # Still on registration page after timeout = likely failed
    log.warning("Still on registration page after timeout - assuming failure")
    return False


def _wait_and_click(element, target_time: datetime, action_name: str = "click"):
    """Wait until target time and click the element with high precision."""
    log.info(f"Preparing to {action_name}...")

    delay = (target_time - datetime.now()).total_seconds()

    if delay < 0:
        log.warning(f"Target time already passed, clicking immediately")
    else:
        hours = int(delay // 3600)
        minutes = int((delay % 3600) // 60)
        seconds = int(delay % 60)
        log.info(f"Waiting {hours}h {minutes}m {seconds}s until target time ({target_time.strftime('%H:%M:%S')})")

        last_log_time = None
        last_log_hour = None
        while True:
            remaining = (target_time - datetime.now()).total_seconds()
            if remaining <= 0.1:
                break

            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            seconds = int(remaining % 60)

            current_remaining_int = int(remaining)

            # Logging cadence:
            # - > 1 hour: log once when the hour countdown changes
            # - <= 1 minute: log every 30 seconds
            # - otherwise: log every 30 seconds
            if remaining > 3600:
                current_hour = int(remaining // 3600)
                should_log = last_log_hour is None or current_hour != last_log_hour
                if should_log:
                    last_log_hour = current_hour
            elif remaining <= 60:
                should_log = (
                    last_log_time is None or
                    (current_remaining_int % 30 == 0 and current_remaining_int != last_log_time)
                )
            else:
                should_log = (
                    last_log_time is None or
                    (current_remaining_int % 30 == 0 and current_remaining_int != last_log_time)
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
    element.evaluate("el => el.click()")

    click_time = datetime.now()
    diff_ms = (click_time - target_time).total_seconds() * 1000
    log.info(f"CLICKED at {click_time.strftime('%H:%M:%S.%f')[:-3]} (diff: {diff_ms:+.1f}ms)")


def _wait_and_reload(page, target_time: datetime):
    """Wait until target time and reload the page at that moment."""
    log.info("Preparing to refresh page at designated time...")

    delay = (target_time - datetime.now()).total_seconds()
    if delay < 0:
        log.warning("Target time already passed, skipping refresh")
        return

    hours = int(delay // 3600)
    minutes = int((delay % 3600) // 60)
    seconds = int(delay % 60)
    log.info(f"Waiting {hours}h {minutes}m {seconds}s until refresh ({target_time.strftime('%H:%M:%S')})")

    last_log_time = None
    last_log_hour = None
    while True:
        remaining = (target_time - datetime.now()).total_seconds()
        if remaining <= 0.1:
            break

        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        seconds = int(remaining % 60)

        current_remaining_int = int(remaining)

        # Logging cadence mirrors _wait_and_click:
        # - > 1 hour: log once when the hour countdown changes
        # - <= 1 minute: log every 30 seconds
        # - otherwise: log every 30 seconds
        if remaining > 3600:
            current_hour = int(remaining // 3600)
            should_log = last_log_hour is None or current_hour != last_log_hour
            if should_log:
                last_log_hour = current_hour
        elif remaining <= 60:
            should_log = (
                last_log_time is None or
                (current_remaining_int % 30 == 0 and current_remaining_int != last_log_time)
            )
        else:
            should_log = (
                last_log_time is None or
                (current_remaining_int % 30 == 0 and current_remaining_int != last_log_time)
            )
        if should_log:
            log.info(f"  {hours:02d}:{minutes:02d}:{seconds:02d} remaining...")
            last_log_time = current_remaining_int

        sleep_time = min(1.0, remaining - 0.1)
        if sleep_time > 0:
            time.sleep(sleep_time)

    while datetime.now() < target_time:
        pass

    page.reload(wait_until="domcontentloaded")
    click_time = datetime.now()
    diff_ms = (click_time - target_time).total_seconds() * 1000
    log.info(f"REFRESHED at {click_time.strftime('%H:%M:%S.%f')[:-3]} (diff: {diff_ms:+.1f}ms)")


def _compute_offsets(attempts: int) -> list[int]:
    """
    Compute offsets (ms) relative to the target time.
    For open play, all attempts refresh right on the designated time (0ms).
    """
    if attempts <= 1:
        return [0]
    return [0 for _ in range(attempts)]


def _run_once(
    wait_until_time: str | None,
    click_at: str | None,
    register_at: str | None,
    register_at_dt: datetime | None,
    no_wait: bool,
    date: str,
    email: str | None,
    password: str | None,
):
    """Single run of the open play flow (used by main and parallel workers)."""
    # Set credentials in environment for login module
    if email:
        os.environ["EMAIL"] = email
    if password:
        os.environ["PASSWORD"] = password

    # Parse wait time if provided (validate early, but wait later after login)
    wait_target = None
    if wait_until_time:
        try:
            wait_target = parse_wait_time(wait_until_time)
        except ValueError as e:
            raise typer.BadParameter(str(e))

    # Parse the booking date
    booking_date = parse_booking_date(date)
    booking_date_str = booking_date.strftime("%a %m/%d")  # e.g., "Sat 12/14"

    # Open play events are only on Tuesdays (1) and Thursdays (3)
    day_of_week = booking_date.weekday()
    if day_of_week not in (1, 3):
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

    # Login first, then wait - so we're ready to go when the time comes
    login()
    select_booking_date(booking_date)

    # Now wait for the target time (after login so we're ready)
    if wait_target:
        log.info(f"Waiting until {wait_target.strftime('%H:%M:%S')} before looking for event...")
        wait_until(wait_target)

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
        _save_debug_snapshot("event_not_found")
        raise Exception(
            f"Could not find any Pickleball Open Play event on {booking_date_str}.\n"
            f"  Intermediate events are typically on Tuesdays, Thursday events on Thursdays.\n"
            f"  The event may not be scheduled for this day, or registration hasn't opened.\n"
            f"  Original error: {e}"
        )

    # Handle click timing for Details
    if no_wait:
        log.info("  --no-wait specified, clicking immediately")
        details_link.click()
        log.info("  Clicked Details")
    elif click_at:
        try:
            target_click_time = parse_wait_time(click_at)
        except ValueError as e:
            raise typer.BadParameter(f"Invalid --click-at time: {e}")
        _wait_and_click(details_link, target_click_time, "click Details")
    else:
        details_link.click()
        log.info("  Clicked Details")

    # If instructed, wait on the details page until the designated time, then refresh
    if register_at_dt:
        log.info(f"Waiting on details page until {register_at_dt.strftime('%H:%M:%S')} then refreshing...")
        _wait_and_reload(page, register_at_dt)
    elif register_at:
        try:
            target_register_time = parse_wait_time(register_at)
        except ValueError as e:
            raise typer.BadParameter(f"Invalid --register-at time: {e}")
        log.info(f"Waiting on details page until {target_register_time.strftime('%H:%M:%S')} then refreshing...")
        _wait_and_reload(page, target_register_time)

    log.info("Clicking Register link...")
    try:
        register_link = page.locator("a:has-text('Register')").first
        register_link.wait_for(timeout=10000)
    except Exception as e:
        _save_debug_snapshot("register_link_not_found")
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
        _save_debug_snapshot("finalize_button_not_found")
        raise Exception(
            f"Could not find 'Finalize Registration' button.\n"
            f"  The registration form may not have loaded correctly.\n"
            f"  Original error: {e}"
        )

    finalize_button.click()

    # Wait for the registration result
    log.info("Waiting for registration result...")
    registration_succeeded = _wait_for_registration_result(page)

    # Save debug snapshot after finalize
    log.info("Saving debug snapshot after finalize...")
    _save_debug_snapshot("after_finalize")

    if not registration_succeeded:
        raise Exception(
            f"Registration may have failed for '{event_name}'.\n"
            f"  Check debug snapshots for details.\n"
        )

    log.info("=" * 50)
    log.info(f"SUCCESS! Registered for: {event_name}")
    log.info("=" * 50)

    # Notify Discord, but don't let notification failures mark the run as failed
    try:
        notify_open_play_success(event_name, booking_date_str)
    except Exception as e:
        log.warning(f"notify_open_play_success failed: {e}")

    # Close browser without failing the run if teardown has issues
    log.info("Closing browser...")
    try:
        close_browser()
    except Exception as e:
        log.warning(f"close_browser failed: {e}")


def _parallel_worker(
    wait_until_time: str | None,
    click_at: str | None,
    register_at_dt: datetime | None,
    register_at: str | None,
    no_wait: bool,
    date: str,
    email: str | None,
    password: str | None,
    click_offset_ms: int,
    worker_id: str,
    result_queue: Queue,
):
    # Separate logging format for workers
    logging.basicConfig(
        level=logging.INFO,
        format=f'%(asctime)s [{worker_id}] %(message)s',
        datefmt='%H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True
    )
    worker_log = logging.getLogger(__name__)

    try:
        # Adjust register time by offset
        adjusted_register_dt = None
        if register_at_dt:
            adjusted_register_dt = register_at_dt + timedelta(milliseconds=click_offset_ms)
            worker_log.info(
                f"Worker offset {click_offset_ms:+d}ms => register at {adjusted_register_dt.strftime('%H:%M:%S.%f')[:-3]}"
            )

        _run_once(
            wait_until_time=wait_until_time,
            click_at=click_at,
            register_at=register_at,
            register_at_dt=adjusted_register_dt,
            no_wait=no_wait,
            date=date,
            email=email,
            password=password,
        )
        result_queue.put({"worker": worker_id, "success": True})
    except Exception as e:
        worker_log.error(f"Worker error: {e}")
        result_queue.put({"worker": worker_id, "success": False, "error": str(e)})
    finally:
        try:
            close_browser()
        except:
            pass


@app.command()
def main(
    wait_until_time: Annotated[
        str | None,
        typer.Option(
            "--wait-until", "-w",
            help="Wait until this time before starting. Formats: 07:00, tomorrow 07:00, +1d 07:00, 2025-12-14 07:00",
        ),
    ] = None,
    click_at: Annotated[
        str | None,
        typer.Option(
            "--click-at", "-c",
            help="Wait until this time before clicking Details. Formats: 07:00, tomorrow 07:00, +1d 07:00",
        ),
    ] = None,
    register_at: Annotated[
        str | None,
        typer.Option(
            "--register-at", "-r",
            help="After opening Details, wait on the details page until this time, then refresh and proceed. Formats: 07:00, tomorrow 07:00, +1d 07:00",
        ),
    ] = None,
    no_wait: Annotated[
        bool,
        typer.Option(
            "--no-wait",
            help="Skip waiting and click immediately (useful for testing)",
        ),
    ] = False,
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
    parallel: Annotated[
        bool,
        typer.Option(
            "--parallel", "-p",
            help="Run multiple parallel attempts (staggered around the target time)",
        ),
    ] = False,
    attempts: Annotated[
        int,
        typer.Option(
            "--attempts", "-a",
            help="Number of parallel attempts (requires --parallel)",
        ),
    ] = 3,
):
    """Register for an Open Play event."""
    # Default: wait on details page until 19:00 (unless overridden or no_wait)
    if not no_wait and not register_at:
        register_at = "19:00"

    if parallel and attempts > 1:
        offsets_ms = _compute_offsets(attempts)
        base_register_dt = None
        if register_at:
            try:
                base_register_dt = parse_wait_time(register_at)
            except ValueError as e:
                raise typer.BadParameter(f"Invalid --register-at time: {e}")

        log.info(f"Parallel mode: attempts={attempts}, offsets={offsets_ms}")
        result_queue: Queue = Queue()
        processes: list[Process] = []

        # Compute a timeout that accounts for waiting until the register time.
        # Default to at least 10 minutes; add 5 minutes buffer after the target time if known.
        join_timeout_seconds = 600
        if base_register_dt:
            remaining = (base_register_dt - datetime.now()).total_seconds()
            if remaining > 0:
                join_timeout_seconds = max(join_timeout_seconds, remaining + 300)

        for idx, offset in enumerate(offsets_ms):
            worker_id = f"P{idx+1}"
            p = Process(
                target=_parallel_worker,
                args=(
                    wait_until_time,
                    click_at,
                    base_register_dt,
                    register_at,
                    no_wait,
                    date,
                    email,
                    password,
                    offset,
                    worker_id,
                    result_queue,
                ),
            )
            processes.append(p)
            p.start()
            log.info(f"Started worker {worker_id} (offset {offset:+d}ms)")

        for p in processes:
            p.join(timeout=join_timeout_seconds)
            if p.is_alive():
                log.warning(f"Worker pid {p.pid} timed out after {join_timeout_seconds:.0f}s, terminating...")
                p.terminate()
                p.join(timeout=5)

        results = []
        while not result_queue.empty():
            results.append(result_queue.get_nowait())

        successes = [r for r in results if r.get("success")]
        if successes:
            log.info(f"Parallel success by {successes[0]['worker']}")
            return
        else:
            errors = ", ".join(f"{r.get('worker')}: {r.get('error')}" for r in results if r.get("error"))
            raise Exception(f"All parallel attempts failed. {errors}")

    # Single attempt
    _run_once(
        wait_until_time=wait_until_time,
        click_at=click_at,
        register_at=register_at,
        register_at_dt=None,
        no_wait=no_wait,
        date=date,
        email=email,
        password=password,
    )


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

        # Try to save debug snapshot before closing
        try:
            _save_debug_snapshot(f"exception_{error_type}")
        except Exception:
            pass

        notify_failure(f"Open Play registration failed.\n{error_type}: {e}")
        close_browser()
        sys.exit(1)


if __name__ == "__main__":
    run()
