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
        # S = 200.0. Deep ITM contract with K = 116.0 (K / S = 0.58, Delta ~ 0.85)
        spot = 200.0
        strikes = [90.0, 116.0, 140.0, 170.0, 200.0, 240.0, 280.0]

        # In Strategy 1 mode, window is [0.50S, 0.95S] -> [100.0, 190.0]
        window_strat1 = get_strike_window(spot, active_strategies=["deep_itm"])
        self.assertAlmostEqual(window_strat1[0], 100.0)
        self.assertAlmostEqual(window_strat1[1], 190.0)

        kept_strat1 = filter_strikes(strikes, spot, active_strategies=["deep_itm"])
        # K=116.0 must be preserved!
        self.assertIn(116.0, kept_strat1)

        # Contrast with naive old v1 filter [0.65S, 1.35S] -> [130.0, 270.0]
        # In naive window, 116.0 was discarded!
        naive_window = (0.65 * spot, 1.35 * spot)
        self.assertNotIn(116.0, [k for k in strikes if naive_window[0] <= k <= naive_window[1]])

    def test_level3_union_window(self):
        from src.leaps_scanner.data.funnel import get_strike_window, filter_strikes
        # When both Strategy 1 ([0.50S, 0.95S]) and Strategy 4 ([0.70S, 1.35S]) are active,
        # Union window is [0.50S, 1.35S]
        spot = 100.0
        low, high = get_strike_window(spot, active_strategies=["deep_itm", "unusual_flow"])
        self.assertAlmostEqual(low, 50.0)
        self.assertAlmostEqual(high, 135.0)

if __name__ == "__main__":
    unittest.main()
