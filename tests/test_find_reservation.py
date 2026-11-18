import importlib.util
import unittest
from datetime import date

from playwright.sync_api import sync_playwright


def card(rid: str, when: str, court: str) -> str:
    """One booking card in the markup My Bookings renders."""
    return f"""
    <div class="booking-card-desktop booking-card-desktop-{rid} acss-1mn0lef">
      <div class="booking-card-desktop-content">
        <div class="booking-card-desktop-header"><h5>Pickleball Court Doubles Reservation</h5></div>
        <div class="booking-card-desktop-content-list">
          <div class="row-text">{when},  8:00 PM - 11:00 PM</div>
          <div class="row-text">Pat Doe, Sam Roe</div>
          <div class="row-text">{court}</div>
        </div>
        <div class="booking-card-desktop-actions"><button>Edit Reservation</button></div>
      </div>
    </div>"""


def bookings(*cards: str, found: int | None = None) -> str:
    count = len(cards) if found is None else found
    return f'<div><div>{count} Bookings Found</div><div>{"".join(cards)}</div></div>'


TONIGHT = card("64133785", "Wed, Nov 18th", "Pickleball Court 6A (Bubble B)")
NEXT_WEEK = card("64403810", "Mon, Nov 23rd", "Pickleball Court #8B (Bubble B)")


class MatchReservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def match(self, html: str, day: date, court: str):
        self.assertIsNotNone(importlib.util.find_spec("utils.cr_roster"))
        from utils import cr_roster

        self.assertTrue(hasattr(cr_roster, "match_reservation"), "cr_roster has no match_reservation")
        self.page.set_content(html)
        return cr_roster.match_reservation(self.page, day, court)

    def test_reads_the_id_from_the_card_class(self):
        html = bookings(NEXT_WEEK, TONIGHT)
        self.assertEqual(self.match(html, date(2026, 11, 18), "6A"), "64133785")

    def test_matches_a_court_written_with_a_hash(self):
        html = bookings(TONIGHT, NEXT_WEEK)
        self.assertEqual(self.match(html, date(2026, 11, 23), "8B"), "64403810")

    def test_none_when_no_card_has_that_court_on_that_day(self):
        html = bookings(TONIGHT, NEXT_WEEK)
        self.assertIsNone(self.match(html, date(2026, 11, 18), "8B"))
        self.assertIsNone(self.match(html, date(2026, 11, 23), "6A"))

    def test_court_must_match_whole(self):
        html = bookings(card("1", "Wed, Nov 18th", "Pickleball Court 6AB"))
        self.assertIsNone(self.match(html, date(2026, 11, 18), "6A"))

    def test_none_when_the_list_is_empty(self):
        self.assertIsNone(self.match(bookings(), date(2026, 11, 18), "6A"))

    def test_raises_when_bookings_exist_but_no_card_is_read(self):
        # Cards the parser can't see would otherwise look like a missing reservation.
        html = bookings('<div class="booking-card-mobile">Wed, Nov 18th Court 6A</div>', found=2)
        with self.assertRaisesRegex(RuntimeError, "2 bookings"):
            self.match(html, date(2026, 11, 18), "6A")


if __name__ == "__main__":
    unittest.main()
