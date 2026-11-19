import importlib.util
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from playwright.sync_api import sync_playwright

from utils import booking_date
from utils.exceptions import DateSelectionError

FIXTURE = Path(__file__).parent / "fixtures" / "bookings_date_picker.html"

# The picker input with no day label beside it.
INPUT_ONLY = """
<div><div class="ant-picker"><div class="ant-picker-input">
  <input data-testid="scheduler-date-picker" value="11/18/2026">
</div></div></div>"""


class DatePickerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        for name in ("LABEL_TIMEOUT_MS", "DROPDOWN_TIMEOUT_MS"):
            patcher = mock.patch.object(booking_date, name, 300, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def open_picker_error(self, html: str) -> Exception:
        self.page.set_content(html)
        try:
            booking_date._open_date_picker(self.page)
        except Exception as e:
            return e
        self.fail("_open_date_picker raised nothing")

    def test_label_and_input_each_match_one_element_on_the_bookings_page(self):
        self.page.set_content(FIXTURE.read_text())
        self.assertEqual(self.page.locator(booking_date.DATE_INPUT).count(), 1)
        label = self.page.locator(booking_date.DATE_LABEL)
        self.assertEqual(label.count(), 1)
        self.assertEqual(label.inner_text().strip(), "Wednesday, November 18")

    def test_missing_label_reports_label_not_found_with_counts(self):
        err = self.open_picker_error(INPUT_ONLY)
        self.assertIsInstance(err, DateSelectionError)
        self.assertIn("date picker label not found", str(err))
        self.assertIn("label matches: 0, input matches: 1", str(err))

    def test_duplicate_labels_report_label_not_found_with_counts(self):
        page = FIXTURE.read_text()
        err = self.open_picker_error(page + page)
        self.assertIsInstance(err, DateSelectionError)
        self.assertIn("label matches: 2, input matches: 2", str(err))

    def test_dropdown_that_never_opens_reports_it(self):
        err = self.open_picker_error(FIXTURE.read_text())
        self.assertIsInstance(err, DateSelectionError)
        self.assertIn("date picker dropdown did not open", str(err))
        self.assertNotIn("label not found", str(err))

    def test_final_failed_attempt_saves_a_snapshot_even_when_tracing(self):
        self.assertIsNotNone(importlib.util.find_spec("utils.debug_snapshot"))
        from utils import debug_snapshot

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        debug_dir = Path(tmp.name)
        self.page.route("**/*", lambda route: route.fulfill(body=INPUT_ONLY, content_type="text/html"))
        env = {"HEADLESS": "true", "ENABLE_TRACING": "true"}
        with mock.patch.object(booking_date, "get_page", return_value=self.page), \
             mock.patch.object(debug_snapshot, "DEBUG_DIR", debug_dir), \
             mock.patch.dict(os.environ, env):
            with self.assertRaises(DateSelectionError):
                booking_date.select_booking_date(datetime(2026, 11, 20))

        saved = sorted(p.suffix for p in debug_dir.glob("*date_picker*"))
        self.assertEqual(saved, [".html", ".png"])
        self.assertIn('data-testid="scheduler-date-picker"', next(debug_dir.glob("*.html")).read_text())


if __name__ == "__main__":
    unittest.main()
