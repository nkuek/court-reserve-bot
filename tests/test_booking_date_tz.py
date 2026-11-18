import unittest
from datetime import datetime, timedelta
from unittest import mock

from tests.clock import frozen_datetime, host_timezone
from constants import LOCAL_TZ, MAX_DAYS_AHEAD
import constants
from utils import booking_date

# 23:30 Eastern is already the next day in Tokyo.
FROZEN = frozen_datetime(datetime(2026, 11, 17, 23, 30, tzinfo=LOCAL_TZ))


class BookingDateTimezoneTest(unittest.TestCase):
    def setUp(self):
        tz = host_timezone("Asia/Tokyo")
        tz.__enter__()
        self.addCleanup(tz.__exit__, None, None, None)
        for module in (booking_date, constants):
            patcher = mock.patch.object(module, "datetime", FROZEN)
            patcher.start()
            self.addCleanup(patcher.stop)

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
