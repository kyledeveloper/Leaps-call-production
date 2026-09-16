import os
import tempfile
import time
import unittest
from pathlib import Path

from tests.python.conftest import block_network
from src.leaps_scanner.data.store.daily_bars import DailyBarCache, market_session_date
from src.leaps_scanner.data.store.prices import PriceBar
from src.leaps_scanner.data.public_delayed import RateLimiter


def _bar(day: str, close: float = 100.0) -> PriceBar:
    return PriceBar(trade_date=day, close=close, high=close + 1, low=close - 1, volume=1e6, open=close)


class TestDailyBarCache(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_hit_same_session_miss_next_session(self):
        session = {"d": "2026-09-16"}
        cache = DailyBarCache(session_date_fn=lambda: session["d"])
        cache.put("aapl", 331.0, [_bar("2026-09-15")], 0.005)
        hit = cache.get("AAPL")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.spot, 331.0)
        self.assertEqual(len(hit.bars), 1)

        session["d"] = "2026-09-17"
        self.assertIsNone(cache.get("AAPL"))

    def test_persist_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "daily_bars.json")
            cache = DailyBarCache(persist_path=path, session_date_fn=lambda: "2026-09-16")
            cache.put("MSFT", 420.0, [_bar("2026-09-15", 419.0)], 0.01)
            cache.flush()
            self.assertTrue(Path(path).exists())
            loaded = DailyBarCache(persist_path=path, session_date_fn=lambda: "2026-09-16")
            hit = loaded.get("msft")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.spot, 420.0)
            self.assertEqual(hit.bars[0].close, 419.0)

    def test_corrupt_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "daily_bars.json")
            Path(path).write_text("{not-json", encoding="utf-8")
            cache = DailyBarCache(persist_path=path, session_date_fn=lambda: "2026-09-16")
            self.assertIsNone(cache.get("AAPL"))

    def test_market_session_date_is_iso(self):
        day = market_session_date()
        self.assertRegex(day, r"^\d{4}-\d{2}-\d{2}$")


class TestRateLimiter(unittest.TestCase):
    def test_zero_interval_does_not_sleep(self):
        lim = RateLimiter(0.0)
        t0 = time.monotonic()
        for _ in range(5):
            lim.wait()
        self.assertLess(time.monotonic() - t0, 0.05)

    def test_min_interval_spaces_calls(self):
        lim = RateLimiter(0.04)
        t0 = time.monotonic()
        lim.wait()
        lim.wait()
        lim.wait()
        self.assertGreaterEqual(time.monotonic() - t0, 0.07)


if __name__ == "__main__":
    unittest.main()
