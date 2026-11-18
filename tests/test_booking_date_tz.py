import importlib.util
import os
import sys
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

# The date logic needs no browser. Stubs let the test run where Playwright is not installed.
for name in ("playwright", "playwright_stealth"):
    if importlib.util.find_spec(name) is None:
        sys.modules[name] = mock.MagicMock()
        sys.modules[f"{name}.sync_api"] = mock.MagicMock()

from constants import LOCAL_TZ, MAX_DAYS_AHEAD
from utils import booking_date

# 23:30 Eastern is already the next day in Tokyo.
FROZEN = datetime(2026, 11, 17, 23, 30, tzinfo=LOCAL_TZ)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN.astimezone(tz) if tz else FROZEN.astimezone().replace(tzinfo=None)


class BookingDateTimezoneTest(unittest.TestCase):
    def setUp(self):
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        self.addCleanup(self._restore_tz, old_tz)
        patcher = mock.patch.object(booking_date, "datetime", FrozenDatetime)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _restore_tz(old_tz):
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    def test_latest_is_eastern_date_when_host_is_ahead(self):
        expected = (datetime(2026, 11, 17) + timedelta(days=MAX_DAYS_AHEAD)).strftime("%Y-%m-%d")
        self.assertEqual(booking_date.parse_booking_date("latest").strftime("%Y-%m-%d"), expected)

    def test_select_booking_date_labels_eastern_latest_as_latest(self):
        eastern_latest = datetime(2026, 11, 17) + timedelta(days=MAX_DAYS_AHEAD)
        with mock.patch.object(booking_date, "get_page"), \
             mock.patch.object(booking_date, "_attempt_date_selection"), \
             self.assertLogs(booking_date.log, level="INFO") as logs:
            booking_date.select_booking_date(eastern_latest)
        self.assertTrue(any("latest available" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
