import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playwright.sync_api import sync_playwright


class SaveDebugSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("utils.debug_snapshot"))
        from utils import debug_snapshot

        self.module = debug_snapshot
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.debug_dir = Path(tmp.name)
        patcher = mock.patch.object(debug_snapshot, "DEBUG_DIR", self.debug_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.set_content("<p>bookings</p>")

    def test_tracing_skips_the_snapshot_unless_the_caller_opts_out(self):
        with mock.patch.dict(os.environ, {"HEADLESS": "true", "ENABLE_TRACING": "true"}):
            self.assertEqual(self.module.save_debug_snapshot("skipped", page=self.page), (None, None))
            self.assertEqual(list(self.debug_dir.iterdir()), [])
            png, html = self.module.save_debug_snapshot("kept", page=self.page, skip_when_tracing=False)
        self.assertTrue(png.exists())
        self.assertIn("<p>bookings</p>", html.read_text())

    def test_cleanup_prunes_only_files_with_the_callers_prefix(self):
        keep = self.module.KEEP_PER_TYPE
        for i in range(keep + 3):
            old = self.debug_dir / f"openplay_2020_{i:02d}.png"
            old.write_bytes(b"")
            os.utime(old, (i, i))
        other = self.debug_dir / "2020_court.png"
        other.write_bytes(b"")
        os.utime(other, (0, 0))

        with mock.patch.dict(os.environ, {"HEADLESS": "true", "ENABLE_TRACING": "false"}):
            png, _ = self.module.save_debug_snapshot("event_not_found", page=self.page, prefix="openplay_")

        self.assertTrue(png.name.startswith("openplay_"))
        self.assertTrue(other.exists())
        self.assertEqual(len(list(self.debug_dir.glob("openplay_*.png"))), keep)
        self.assertTrue(png.exists())


if __name__ == "__main__":
    unittest.main()
