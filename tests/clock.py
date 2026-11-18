"""Shared helpers for tests that freeze the clock and change the host timezone."""

import importlib.util
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from unittest import mock

# The date logic needs no browser. Stubs let tests run where Playwright is not installed.
for _name in ("playwright", "playwright_stealth"):
    if importlib.util.find_spec(_name) is None:
        sys.modules[_name] = mock.MagicMock()
        sys.modules[f"{_name}.sync_api"] = mock.MagicMock()


def frozen_datetime(instant: datetime):
    """A datetime subclass whose now() returns the given aware instant."""

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.astimezone().replace(tzinfo=None)

    return Frozen


@contextmanager
def host_timezone(name: str):
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
