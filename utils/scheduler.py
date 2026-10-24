"""Helpers for the bookings page scheduler, an Ant Design grid that renders only the rows near its scroll position."""
import logging

from constants import SLOT_BUTTON

log = logging.getLogger(__name__)

_SCROLL_JS = """(args) => {
    let n = document.querySelector(args.sel);
    while (n && !(n.scrollHeight > n.clientHeight && /auto|scroll/.test(getComputedStyle(n).overflowY))) n = n.parentElement;
    if (!n) return null;
    if (args.top !== null) n.scrollTop = args.top;
    return {top: n.scrollTop, height: n.clientHeight, total: n.scrollHeight};
}"""

_READ_JS = """(sel) => [...document.querySelectorAll(sel)].map(b => ({
    court: b.dataset.courtLabel, start: b.dataset.slotStart, text: b.textContent.trim(),
}))"""


def close_date_picker(page):
    """Dismiss the calendar popup, which stays open after a day is picked and covers the left columns."""
    page.keyboard.press("Escape")
    page.wait_for_selector(".ant-picker-dropdown:not(.ant-picker-dropdown-hidden)", state="detached", timeout=3000)


def collect_open_slots(page) -> dict[tuple[str, str], str]:
    """
    Scroll the scheduler top to bottom and gather every open slot.

    Returns {(court label, "HH:MM" start): button text}. A single read misses the rows outside the viewport.
    """
    found: dict[tuple[str, str], str] = {}
    state = page.evaluate(_SCROLL_JS, {"sel": SLOT_BUTTON, "top": 0})
    top = 0
    while True:
        page.wait_for_timeout(150)
        for s in page.evaluate(_READ_JS, SLOT_BUTTON):
            found[(s["court"], s["start"])] = s["text"]
        if not state or top + state["height"] >= state["total"]:
            break
        top += max(state["height"] // 2, 100)
        state = page.evaluate(_SCROLL_JS, {"sel": SLOT_BUTTON, "top": top})
    return found


def reveal(page, locator, timeout_ms: int = 5000) -> bool:
    """Scroll the scheduler until the locator has a rendered match. Returns False when no scroll position shows it."""
    state = page.evaluate(_SCROLL_JS, {"sel": SLOT_BUTTON, "top": 0})
    top = 0
    while True:
        page.wait_for_timeout(100)
        if locator.count() > 0:
            return True
        if not state or top + state["height"] >= state["total"]:
            return False
        top += max(state["height"] // 2, 100)
        state = page.evaluate(_SCROLL_JS, {"sel": SLOT_BUTTON, "top": top})
