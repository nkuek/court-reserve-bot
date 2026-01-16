import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import get_page, close_browser, BASE_URL, start_tracing_if_enabled
from utils.exceptions import LoginError

log = logging.getLogger(__name__)

# Default retry settings
MAX_LOGIN_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def _attempt_login(page, email: str, password: str) -> bool:
    """Attempt a single login. Returns True on success, raises on failure."""
    page.goto(f"{BASE_URL}/Account/Login")
    page.wait_for_load_state("domcontentloaded")

    # Wait for form to be ready (faster than fixed 2s wait)
    form = page.locator("form").first
    form.wait_for(timeout=10000)
    inputs = form.locator("input").all()

    # Filter to visible inputs
    visible_inputs = [i for i in inputs if i.get_attribute("type") not in ["hidden"]]

    if len(visible_inputs) >= 2:
        # First input is email, second is password
        visible_inputs[0].fill(email)
        visible_inputs[1].fill(password)
    else:
        raise LoginError("Could not find login form inputs")

    # Click submit button
    submit_btn = form.locator("button[type='submit'], input[type='submit'], button:has-text('Log'), button:has-text('Sign')").first
    submit_btn.click()

    # Wait for redirect (login completion)
    log.info("  Waiting for login to complete...")
    try:
        page.wait_for_url(lambda url: "Login" not in url and "LogIn" not in url, timeout=15000)
        return True
    except Exception:
        current_url = page.url
        if "Login" in current_url or "LogIn" in current_url:
            raise LoginError(f"Login failed - still on login page: {current_url}")
        # If we're not on login page, we might have succeeded
        return True


def login(max_retries: int = MAX_LOGIN_RETRIES, retry_delay: float = RETRY_DELAY_SECONDS):
    """Log in to CourtReserve using credentials from environment variables.

    Args:
        max_retries: Maximum number of login attempts (default: 3)
        retry_delay: Seconds to wait between retries (default: 5)
    """
    email = os.environ.get("EMAIL")
    password = os.environ.get("PASSWORD")

    missing = []
    if not email:
        missing.append("EMAIL")
    if not password:
        missing.append("PASSWORD")

    if missing:
        raise LoginError(
            f"Missing required environment variable(s): {', '.join(missing)}.\n"
            f"  Please set these in your .env file:\n"
            f"    EMAIL=your_email@example.com\n"
            f"    PASSWORD=your_password"
        )

    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            page = get_page()
            log.info(f"Logging in... (attempt {attempt}/{max_retries})")

            if _attempt_login(page, email, password):
                log.info("  Login successful")
                # Start tracing only after successful login (to avoid capturing credentials)
                start_tracing_if_enabled()
                return

        except Exception as e:
            last_error = e
            log.warning(f"  Login attempt {attempt} failed: {e}")

            if attempt < max_retries:
                log.info(f"  Retrying in {retry_delay} seconds with fresh browser...")
                # Close browser to get a fresh session (helps avoid bot detection)
                close_browser()
                time.sleep(retry_delay)
            else:
                log.error(f"  All {max_retries} login attempts failed")

    # All retries exhausted
    raise LoginError(
        f"Login failed after {max_retries} attempts.\n"
        f"  Last error: {last_error}\n"
        f"  This may be due to bot detection. Try again later or check credentials."
    )
