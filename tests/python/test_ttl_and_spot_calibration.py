import os
import tempfile
import time
import unittest
from pathlib import Path

from tests.python.conftest import block_network
from src.leaps_scanner.data.store.daily_bars import DailyBarCache
from src.leaps_scanner.data.store.prices import PriceBar, PriceStore
from src.leaps_scanner.data.public_delayed import parse_nasdaq_last_trade


def _bar(day: str, close: float = 100.0) -> PriceBar:
    return PriceBar(trade_date=day, close=close, high=close + 2, low=close - 2, volume=1e6, open=close)


class TestTTLAndSpotCalibration(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_daily_bar_cache_ttl_expiration(self):
        fake_time = 1000.0
        cache = DailyBarCache(
            session_date_fn=lambda: "2026-09-16",
            time_fn=lambda: fake_time,
            ttl_seconds=3600.0
        )
        cache.put("AAPL", 100.0, [_bar("2026-09-15", 100.0)], 0.01)

        # Within 1 hour (3600s), cache hits
        fake_time = 1000.0 + 1800.0
        hit = cache.get("AAPL")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.spot, 100.0)

        # After 1 hour (e.g. 3601s), cache expires
        fake_time = 1000.0 + 3601.0
        miss = cache.get("AAPL")
        self.assertIsNone(miss, "Cache must expire when TTL is exceeded")

    def test_price_store_recalibrates_metrics_with_spot_override(self):
        store = PriceStore()
        # Seed 250 bars with close 100.0
        bars = [_bar(f"2025-01-{i:02d}", 100.0) for i in range(1, 251)]
        store.add_bars("AAPL", bars)

        # Base metrics (spot=100)
        base = store.get_metrics("AAPL")
        self.assertEqual(base.spot, 100.0)
        self.assertAlmostEqual(base.pct_to_200dma, 0.0, places=4)

        # Real-time spot jump to 120 (e.g. from Nasdaq)
        calibrated = store.get_metrics("AAPL", spot_override=120.0)
        self.assertEqual(calibrated.spot, 120.0)
        # 200DMA should reflect the 120 spot
        self.assertGreater(calibrated.pct_to_200dma, 0.15)
        # Drawdown from 52w high: high was 102, now 120 becomes the new high, so drawdown is 0
        self.assertAlmostEqual(calibrated.drawdown_52w_high, 0.0, places=4)
        self.assertEqual(calibrated.high_52w, 120.0)

    def test_parse_nasdaq_last_trade_robustness(self):
        self.assertEqual(parse_nasdaq_last_trade("$150.25"), 150.25)
        self.assertEqual(parse_nasdaq_last_trade("150.25"), 150.25)
        self.assertEqual(parse_nasdaq_last_trade(150.25), 150.25)
        self.assertEqual(parse_nasdaq_last_trade(150), 150.0)
        self.assertIsNone(parse_nasdaq_last_trade(""))
        self.assertIsNone(parse_nasdaq_last_trade(None))


if __name__ == "__main__":
    unittest.main()
