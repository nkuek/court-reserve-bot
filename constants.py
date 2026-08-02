import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext, Playwright
from playwright_stealth import Stealth

# URLs
BASE_URL = "https://app.courtreserve.com"
RESERVATIONS_URL = "https://reservations.courtreserve.com"
BACKEND_URL = "https://backend.courtreserve.com"

# Organization-specific IDs
ORG_ID = "8449"
SCHEDULE_ID = "32125"

# Valid duration options for bookings (in hours)
VALID_DURATIONS = [1.0, 1.5, 2.0, 2.5, 3.0]

# Facility hours
FACILITY_CLOSING_HOUR = 23
FACILITY_CLOSING_MINUTE = 0

# Maximum days in advance for booking
MAX_DAYS_AHEAD = 6

# Global state for browser management
_stealth_ctx_mgr = None
_playwright: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None
_tracing_active: bool = False
_trace_path: Path | None = None
_trace_label: str | None = None


def set_trace_label(label: str):
    """Set a custom label for the trace file (call before login)."""
    global _trace_label
    _trace_label = label


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


def start_tracing_if_enabled():
    """Start tracing after login to avoid capturing credentials."""
    global _tracing_active, _trace_path, _trace_label

    if _tracing_active:
        return

    if os.environ.get("ENABLE_TRACING", "false").lower() == "true":
        context = get_context()
        trace_dir = Path(__file__).parent / "data" / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Use custom label if set, otherwise fall back to PID
        if _trace_label:
            # Sanitize label for filename (replace spaces and special chars)
            safe_label = _trace_label.replace(" ", "_").replace("(", "").replace(")", "").replace("/", "-")
            _trace_path = trace_dir / f"trace_{timestamp}_{safe_label}.zip"
        else:
            pid = os.getpid()
            _trace_path = trace_dir / f"trace_{timestamp}_{pid}.zip"

        context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=True,
        )
        _tracing_active = True
        print(f"[TRACING] Started - will save to {_trace_path}")


def close_browser():
    """Close the browser and clean up resources."""
    global _stealth_ctx_mgr, _playwright, _browser, _context, _page, _tracing_active, _trace_path

    if _page:
        try:
            _page.close()
        except:
            pass
        _page = None

    if _context:
        # Stop tracing and save before closing context
        if _tracing_active and _trace_path:
            try:
                _context.tracing.stop(path=str(_trace_path))
                print(f"[TRACING] Saved to {_trace_path}")
                print(f"[TRACING] View with: npx playwright show-trace {_trace_path}")
            except Exception as e:
                print(f"[TRACING] Failed to save: {e}")
            _tracing_active = False
            _trace_path = None

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
    "Pickleball Court 5D (Bubble B)",
    "Pickleball Court 5B (Bubble B)",
    "Pickleball Court 5A (Bubble B)",
    "Pickleball Court 6A (Bubble B)",
    "Pickleball Court 6B (Bubble B)",
    "Pickleball Court 6C (Bubble B)",
    "Pickleball Court 6D (Bubble B)",
    "Pickleball Court #7A (Bubble B)",
    "Pickleball Court #7B (Bubble B)",
    "Pickleball Court #8A (Bubble B)",
    "Pickleball Court #8B (Bubble B)",
]
