import json
import os
import tempfile
import time
import unittest
from tests.python.conftest import block_network
from src.leaps_scanner.data.universe import SymbologyNormalizer
from src.leaps_scanner.data.rebalancer import DynamicUniverseManager
from src.leaps_scanner.strategies.oversold import (
    evaluate_oversold_underlying,
    OversoldUnderlyingMetrics
)
from src.leaps_scanner.strategies.vol_discount import (
    evaluate_vol_discount_underlying,
    VolDiscountUnderlyingMetrics
)
from src.leaps_scanner.strategies.unusual_flow import (
    evaluate_unusual_flow,
    UnusualFlowInput
)
from src.leaps_scanner.scoring.ranker import MemoryRanker, StrategyCandidate
from src.leaps_scanner.strategies.guards import GuardStatus
from src.leaps_scanner.api.server import AppState


class TestSymbologyAndCacheHotReload(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_oversold_strategy_symbology_defense(self):
        # BRK.B is in S&P 100; caller passes broker-format "BRK-B"
        metrics = OversoldUnderlyingMetrics(
            symbol="BRK-B",
            spot=450.0,
            rsi_14=25.0,
            pct_to_200dma=-0.12,
            drawdown_52w_high=0.18,
            bounce_52w_low=0.04,
            hv_20=0.18,
            bar_count=250,
            is_valid=True
        )
        res = evaluate_oversold_underlying(metrics)
        self.assertEqual(res.symbol, "BRK.B")
        self.assertNotEqual(res.status, GuardStatus.REJECT)
        self.assertNotIn("NOT_IN_CORE_UNIVERSE", res.reasons)

    def test_vol_discount_symbology_defense(self):
        metrics = VolDiscountUnderlyingMetrics(
            symbol="brk-b",
            spot=450.0,
            pct_change_20d=0.02,
            drawdown_52w_high=0.05,
            current_atm_iv=0.15,
            hv_252=0.18,
            iv_percentile=0.15,
            iv_rank=0.12,
            iv_z_score=-1.2,
            valid_history_days=250,
            is_degraded=False
        )
        res = evaluate_vol_discount_underlying(metrics)
        self.assertEqual(res.symbol, "BRK.B")
        self.assertEqual(res.status, GuardStatus.PASS)

    def test_unusual_flow_symbology_defense(self):
        inp = UnusualFlowInput(
            symbol="BRK-B270115C00400000",
            underlying="BRK-B",
            spot=450.0,
            strike=400.0,
            dte=450.0,
            bid=70.0,
            ask=72.0,
            volume=5000,
            open_interest=500,
            is_etf=False,
            liquidity_status=GuardStatus.PASS
        )
        res = evaluate_unusual_flow(inp)
        self.assertEqual(res.symbol, "BRK.B270115C00400000")
        self.assertEqual(res.status, GuardStatus.PASS)

    def test_memory_ranker_symbology_defense(self):
        cand = StrategyCandidate(
            symbol="BRK-B270115C00350000",
            underlying="BRK-B",
            strike=350.0,
            spot=450.0,
            dte=480.0,
            bid=110.0,
            ask=114.0,
            delta=0.82,
            open_interest=1200,
            volume=150,
            valid_history_days=250
        )
        ranker = MemoryRanker([cand])
        boards = ranker.rank_boards(alpha=0.5)
        self.assertGreater(len(boards["deep_itm"]), 0)
        self.assertEqual(boards["deep_itm"][0].underlying, "BRK.B")

    def test_app_state_symbology_defense(self):
        state = AppState(offline_mode=True)
        # Scan with lowercase / hyphen symbol
        count = state.run_scan(["aapl", "brk-b"])
        self.assertGreater(count, 0)
        self.assertEqual(state.candidates[0].underlying, "AAPL")

    def test_dynamic_universe_cache_mtime_hot_reload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = os.path.join(tmp_dir, "universe_cache.json")

            # Process A initializes manager and writes clean cache
            mgr_a = DynamicUniverseManager(cache_path=cache_file, offline_mode=True)
            self.assertEqual(len(mgr_a.get_constituents("djia")), 30)

            # Ensure time difference on filesystem
            time.sleep(0.05)

            # Process B (e.g. CLI rebalance sync) modifies cache file
            mgr_b = DynamicUniverseManager(cache_path=cache_file, offline_mode=True)
            cur_djia = mgr_b.get_constituents("djia")
            # Simulate adding a special test ticker
            new_djia = cur_djia + ["NEWTICKER"]
            mgr_b.apply_rebalance("djia", new_djia, reason="Simulated rebalance")

            # Process A should automatically detect mtime change and hot reload
            reloaded_djia = mgr_a.get_constituents("djia")
            self.assertIn("NEWTICKER", reloaded_djia)
            self.assertEqual(len(reloaded_djia), 31)

            master = mgr_a.get_master_universe()
            self.assertIn("NEWTICKER", master)

            history = mgr_a.get_rebalance_history()
            self.assertGreaterEqual(len(history), 1)
            self.assertIn("NEWTICKER", history[-1]["added"])


if __name__ == "__main__":
    unittest.main()
