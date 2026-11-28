import asyncio
import inspect
import unittest
from unittest import mock

import bot


def _book(private: bool) -> list[str]:
    interaction = mock.Mock()
    interaction.response.send_message = mock.AsyncMock()
    with mock.patch.object(bot, "get_user_credentials", return_value=("a@b.c", "pw")), \
         mock.patch.object(bot, "_run_script", new=mock.Mock()) as run_script, \
         mock.patch.object(bot.asyncio, "create_task"):
        asyncio.run(bot.book.callback(interaction, time="21:00", duration=2.0, private=private))
    return run_script.call_args.args[1]


class BookPrivateFlagTest(unittest.TestCase):
    def test_private_book_passes_private_flag(self):
        self.assertIn("private", inspect.signature(bot.book.callback).parameters)
        self.assertIn("--private", _book(True))

    def test_default_book_omits_private_flag(self):
        cmd = _book(False)
        self.assertNotIn("--private", cmd)
        self.assertEqual(cmd[cmd.index("--time") + 1], "21:00")


if __name__ == "__main__":
    unittest.main()
