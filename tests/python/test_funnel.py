import unittest
from tests.python.conftest import block_network

class TestStrategyAwareFunnel(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_level2_dte_filter(self):
        from src.leaps_scanner.data.funnel import filter_expirations
        # Must only keep DTE >= 250
        expirations = [
            {"date": "2026-03-20", "dte": 180.0},
            {"date": "2026-06-19", "dte": 270.0},
            {"date": "2027-01-15", "dte": 480.0}
        ]
        kept = filter_expirations(expirations, min_dte=250.0)
        self.assertEqual(len(kept), 2)
        self.assertEqual([e["date"] for e in kept], ["2026-06-19", "2027-01-15"])

    def test_level3_strategy_one_preserves_deep_itm_contract(self):
        from src.leaps_scanner.data.funnel import get_strike_window, filter_strikes
        spot = 200.0
        strikes = [90.0, 116.0, 140.0, 170.0, 200.0, 240.0, 280.0]

        # Strategy 1 window is [0.65S, 0.85S] -> [130.0, 170.0]
        window_strat1 = get_strike_window(spot, active_strategies=["deep_itm"])
        self.assertAlmostEqual(window_strat1[0], 130.0)
        self.assertAlmostEqual(window_strat1[1], 170.0)

        kept_strat1 = filter_strikes(strikes, spot, active_strategies=["deep_itm"])
        self.assertIn(140.0, kept_strat1)
        self.assertIn(170.0, kept_strat1)
        # 0.58S is prepaid equity, not a replacement LEAPS
        self.assertNotIn(116.0, kept_strat1)
        self.assertNotIn(200.0, kept_strat1)

    def test_level3_union_window(self):
        from src.leaps_scanner.data.funnel import get_strike_window, filter_strikes
        # Strategy 1 [0.65S, 0.85S] union Strategy 3 [0.50S, 1.25S] -> [0.50S, 1.25S]
        spot = 100.0
        low, high = get_strike_window(spot, active_strategies=["deep_itm", "oversold"])
        self.assertAlmostEqual(low, 50.0)
        self.assertAlmostEqual(high, 125.0)

    def test_prepaid_equity_delta_is_not_scanned(self):
        from src.leaps_scanner.data.funnel import keep_scan_delta
        from src.leaps_scanner.scoring.ranker import StrategyCandidate, MemoryRanker

        self.assertTrue(keep_scan_delta(0.90))
        self.assertFalse(keep_scan_delta(0.901))
        self.assertFalse(keep_scan_delta(0.99))

        keep = StrategyCandidate(
            symbol="AAPL270115C00180000", underlying="AAPL", strike=180.0, spot=220.0,
            dte=480.0, bid=50.0, ask=52.0, delta=0.82, open_interest=1000, volume=80,
        )
        drop = StrategyCandidate(
            symbol="AAPL270115C00100000", underlying="AAPL", strike=100.0, spot=220.0,
            dte=480.0, bid=118.0, ask=122.0, delta=0.97, open_interest=800, volume=40,
        )
        ranker = MemoryRanker([keep, drop])
        board = ranker.rank(alpha=0.5)
        symbols = [x.symbol for x in board]
        self.assertIn("AAPL270115C00180000", symbols)
        self.assertNotIn("AAPL270115C00100000", symbols)

if __name__ == "__main__":
    unittest.main()
