import unittest
from datetime import datetime
from unittest import mock

from tests.clock import frozen_datetime, host_timezone
import constants
from constants import LOCAL_TZ
from utils import booking_date, wait

HOST_ZONES = ("Asia/Tokyo", "America/New_York", "Europe/London")


class EasternClockTest(unittest.TestCase):
    def test_wait_time_of_day_is_eastern_on_any_host(self):
        for zone in HOST_ZONES:
            with self.subTest(host=zone), host_timezone(zone):
                with mock.patch.object(wait, "datetime", frozen_datetime(datetime(2026, 11, 17, 20, 0, tzinfo=LOCAL_TZ))), \
                     mock.patch.object(constants, "datetime", frozen_datetime(datetime(2026, 11, 17, 20, 0, tzinfo=LOCAL_TZ))):
                    target = wait.parse_wait_time("21:00")
                self.assertEqual(target.timestamp(), datetime(2026, 11, 17, 21, 0, tzinfo=LOCAL_TZ).timestamp())

    def test_wait_until_counts_down_in_eastern_on_any_host(self):
        for zone in HOST_ZONES:
            with self.subTest(host=zone), host_timezone(zone):
                instant = datetime(2026, 11, 17, 20, 59, tzinfo=LOCAL_TZ)
                with mock.patch.object(wait, "datetime", frozen_datetime(instant)), \
                     mock.patch.object(constants, "datetime", frozen_datetime(instant)), \
                     mock.patch.object(wait.time, "sleep", side_effect=StopIteration), \
                     self.assertLogs(wait.log, level="INFO") as logs:
                    try:
                        wait.wait_until(datetime(2026, 11, 17, 21, 0))
                    except StopIteration:
                        pass
                self.assertTrue(any("Time remaining: 0h 1m 0s" in line for line in logs.output))

    def test_seconds_until_counts_real_seconds_across_a_dst_change(self):
        self.assertTrue(hasattr(wait, "seconds_until"))
        instant = datetime(2026, 11, 1, 0, 30, tzinfo=LOCAL_TZ)
        with host_timezone("Asia/Tokyo"), \
             mock.patch.object(constants, "datetime", frozen_datetime(instant)), \
             mock.patch.object(wait, "datetime", frozen_datetime(instant)):
            # Clocks fall back at 02:00 EDT, so 02:30 EST is three hours away.
            self.assertEqual(wait.seconds_until(datetime(2026, 11, 1, 2, 30)), 10800)

    def test_booking_opens_at_is_eastern_on_any_host(self):
        self.assertTrue(hasattr(booking_date, "booking_opens_at"))
        for zone in HOST_ZONES:
            with self.subTest(host=zone), host_timezone(zone):
                # A slot starting at 21:00 opens MAX_DAYS_AHEAD days earlier at the same Eastern wall time.
                # Nov 1 is the DST change, so UTC arithmetic would land an hour off.
                opens = booking_date.booking_opens_at(datetime(2026, 11, 6), 21, 0)
                self.assertEqual(opens.timestamp(), datetime(2026, 10, 31, 21, 0, tzinfo=LOCAL_TZ).timestamp())

    def test_slot_is_not_yet_open_30_seconds_early_on_any_host(self):
        self.assertTrue(hasattr(booking_date, "booking_opens_at"))
        instant = datetime(2026, 11, 17, 20, 59, 30, tzinfo=LOCAL_TZ)
        for zone in HOST_ZONES:
            with self.subTest(host=zone), host_timezone(zone), \
                 mock.patch.object(constants, "datetime", frozen_datetime(instant)), \
                 mock.patch.object(wait, "datetime", frozen_datetime(instant)):
                opens = booking_date.booking_opens_at(datetime(2026, 11, 23), 21, 0)
                self.assertEqual(wait.seconds_until(opens), 30)


if __name__ == "__main__":
    unittest.main()
