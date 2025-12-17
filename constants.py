import os
from contextlib import contextmanager
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext, Playwright
from playwright_stealth import Stealth

# URLs
BASE_URL = "https://app.courtreserve.com"
RESERVATIONS_URL = "https://reservations.courtreserve.com"
BACKEND_URL = "https://backend.courtreserve.com"

# Organization-specific IDs
ORG_ID = "8449"
SCHEDULE_ID = "18493"

# Valid duration options for bookings (in hours)
VALID_DURATIONS = [1.0, 1.5, 2.0, 2.5, 3.0]

# Facility hours
FACILITY_CLOSING_HOUR = 23
FACILITY_CLOSING_MINUTE = 0

# Maximum days in advance for booking
MAX_DAYS_AHEAD = 5

# Global state for browser management
_stealth_ctx_mgr = None
_playwright: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None


def _init_playwright():
    """Initialize Playwright with stealth patches."""
    global _stealth_ctx_mgr, _playwright
    
    if _playwright is None:
        stealth = Stealth(
            navigator_platform_override="MacIntel",
            navigator_vendor_override="Google Inc.",
        )
        # Store the context manager so we can exit it later
        _stealth_ctx_mgr = stealth.use_sync(sync_playwright())
        _playwright = _stealth_ctx_mgr.__enter__()
    
    return _playwright


def get_browser() -> Browser:
    """Get or create the browser instance."""
    global _browser
    
    if _browser is None:
        p = _init_playwright()
        headless = os.environ.get("HEADLESS", "false").lower() == "true"
        
        _browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-notifications",
                "--disable-popup-blocking",
            ]
        )
    return _browser


def get_context() -> BrowserContext:
    """Get or create the browser context."""
    global _context
    
    if _context is None:
        browser = get_browser()
        _context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    return _context


def get_page() -> Page:
    """Get or create the page instance (lazy initialization)."""
    global _page
    
    if _page is None:
        context = get_context()
        _page = context.new_page()
    return _page


def close_browser():
    """Close the browser and clean up resources."""
    global _stealth_ctx_mgr, _playwright, _browser, _context, _page
    
    if _page:
        try:
            _page.close()
        except:
            pass
        _page = None
    
    if _context:
        try:
            _context.close()
        except:
            pass
        _context = None
    
    if _browser:
        try:
            _browser.close()
        except:
            pass
        _browser = None
    
    if _stealth_ctx_mgr:
        try:
            _stealth_ctx_mgr.__exit__(None, None, None)
        except:
            pass
        _stealth_ctx_mgr = None
        _playwright = None


@contextmanager
def browser_session():
    """Context manager for a browser session with automatic cleanup."""
    try:
        yield get_page()
    finally:
        close_browser()


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


# Legacy compatibility - alias for migration
def get_driver():
    """Legacy compatibility function - returns the Playwright page.
    
    Note: This is for migration compatibility. New code should use get_page() directly.
    The returned object is a Playwright Page, not a Selenium WebDriver.
    """
    return get_page()
