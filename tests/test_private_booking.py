import asyncio
import inspect
import unittest
from datetime import datetime
from unittest import mock

from utils import booker_prompt, whatsapp

BOOKED = datetime(2026, 12, 3)


class QueueBookingPostTest(unittest.TestCase):
    def setUp(self):
        mock.patch.object(whatsapp.signup_app, "is_configured", return_value=True).start()
        self.add_court = mock.patch.object(whatsapp.signup_app, "add_court").start()
        self.enqueue = mock.patch.object(whatsapp, "enqueue_post", return_value=True).start()
        mock.patch.object(whatsapp, "is_configured", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def test_private_booking_skips_app_and_group(self):
        self.assertIn("private", inspect.signature(whatsapp.queue_booking_post).parameters)
        whatsapp.queue_booking_post(BOOKED, 21, 0, 2.0, "Pickleball Court #8A (Bubble B)", private=True)
        self.assertEqual(self.add_court.call_count, 0)
        self.assertEqual(self.enqueue.call_count, 0)

    def test_shared_booking_lists_court_and_queues_post(self):
        whatsapp.queue_booking_post(BOOKED, 19, 30, 1.5, "Pickleball Court 5C")
        self.assertEqual(self.add_court.call_args.args[1:], (1.5, "5C"))
        self.assertEqual(self.enqueue.call_args.args[0], "2026-12-03")


class AskBookerTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(booker_prompt.signup_app, "is_configured", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = mock.Mock()
        self.user.send = mock.AsyncMock()

    def test_private_booking_sends_no_playing_prompt(self):
        result = {"success": True, "date": "2026-12-03", "court_short": "#7A", "private": True}
        asyncio.run(booker_prompt.ask_booker(self.user, result))
        self.assertEqual(self.user.send.await_count, 0)

    def test_shared_booking_asks_the_booker(self):
        result = {"success": True, "date": "2026-12-04", "court_short": "6B"}
        asyncio.run(booker_prompt.ask_booker(self.user, result))
        self.assertIn("court 6B", self.user.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
