"""Screenshot and HTML dumps of the current page for debugging failed runs."""
import logging
import os
from datetime import datetime
from pathlib import Path

from constants import get_page

log = logging.getLogger(__name__)

DEBUG_DIR = Path(__file__).resolve().parent.parent / "data" / "debug"
KEEP_PER_TYPE = 20


def save_debug_snapshot(suffix: str, page=None, prefix: str = "", skip_when_tracing: bool = True):
    """Save a screenshot and HTML of the page, only in headless mode or when DEBUG_SNAPSHOTS is set."""
    # A trace already records screenshots and DOM for every action.
    if skip_when_tracing and os.environ.get("ENABLE_TRACING", "false").lower() == "true":
        return None, None

    if not (os.environ.get("HEADLESS", "false").lower() == "true" or os.environ.get("DEBUG_SNAPSHOTS")):
        return None, None

    page = page or get_page()
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    screenshot_path = DEBUG_DIR / f"{prefix}{timestamp}_{suffix}.png"
    try:
        page.screenshot(path=screenshot_path)
        log.info(f"  Screenshot saved: {screenshot_path}")
    except Exception as e:
        log.warning(f"  Failed to save screenshot: {e}")
        screenshot_path = None

    html_path = DEBUG_DIR / f"{prefix}{timestamp}_{suffix}.html"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        log.info(f"  HTML saved: {html_path}")
    except Exception as e:
        log.warning(f"  Failed to save HTML: {e}")
        html_path = None

    for pattern in (f"{prefix}*.png", f"{prefix}*.html"):
        files = sorted(DEBUG_DIR.glob(pattern), key=os.path.getmtime)
        for old_file in files[:-KEEP_PER_TYPE]:
            try:
                os.remove(old_file)
            except Exception:
                pass

    return screenshot_path, html_path
