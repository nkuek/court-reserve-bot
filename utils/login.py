import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import get_page, BASE_URL
from utils.exceptions import LoginError

log = logging.getLogger(__name__)


def login():
    """Log in to CourtReserve using credentials from environment variables."""
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

    page = get_page()
    log.info("Logging in...")
    page.goto(f"{BASE_URL}/Account/Login")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)  # Wait for JS to load

    # Find and fill form inputs
    form = page.locator("form").first
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
        log.info("  Login successful")
    except Exception as e:
        current_url = page.url
        if "Login" in current_url or "LogIn" in current_url:
            raise LoginError(f"Login failed - still on login page: {current_url}")
        # If we're not on login page, we might have succeeded
        log.info("  Login successful")
