import unittest
from tests.python.conftest import block_network
from src.leaps_scanner.strategies.guards import GuardStatus


class TestUnusualFlowStrategy(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_institutional_single_stock_flow_pass(self):
        from src.leaps_scanner.strategies.unusual_flow import (
            evaluate_unusual_flow,
            UnusualFlowInput
        )

        flow_input = UnusualFlowInput(
            symbol="AAPL270115C00220000",
            underlying="AAPL",
            spot=220.0,
            strike=220.0,
            dte=360.0,
            bid=19.5,
            ask=20.5,
            volume=600,
            open_interest=150,
            is_etf=False,
            liquidity_status=GuardStatus.PASS
        )
        res = evaluate_unusual_flow(flow_input)
        self.assertEqual(res.status, GuardStatus.PASS)
        self.assertAlmostEqual(res.vol_oi_ratio, 4.0)
        self.assertAlmostEqual(res.dollar_volume, 1_200_000.0)
        self.assertFalse(res.buyer_aggressor_tag)
        self.assertIn("Far-dated flow includes rollovers", res.caveat_notice)

    def test_cheap_otm_lottery_trap_rejected(self):
        from src.leaps_scanner.strategies.unusual_flow import (
            evaluate_unusual_flow,
            UnusualFlowInput
        )

        # High Vol/OI ratio (10.0), but tiny dollar volume ($50,000)
        flow_input = UnusualFlowInput(
            symbol="AAPL270115C00280000",
            underlying="AAPL",
            spot=220.0,
            strike=280.0,
            dte=360.0,
            bid=0.45,
            ask=0.55,
            volume=1000,
            open_interest=100,
            is_etf=False,
            liquidity_status=GuardStatus.PASS
        )
        res = evaluate_unusual_flow(flow_input)
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertTrue(any("LOW_DOLLAR_VOLUME" in r for r in res.reasons))

    def test_etf_flow_thresholds(self):
        from src.leaps_scanner.strategies.unusual_flow import (
            evaluate_unusual_flow,
            UnusualFlowInput
        )

        # SPY ETF requires $1M dollar volume and 2000 contracts for PASS
        # 1500 contracts is WATCH for ETF count
        flow_input = UnusualFlowInput(
            symbol="SPY270115C00550000",
            underlying="SPY",
            spot=550.0,
            strike=550.0,
            dte=400.0,
            bid=34.0,
            ask=36.0,
            volume=1500,
            open_interest=400,
            is_etf=True,
            liquidity_status=GuardStatus.PASS
        )
        res = evaluate_unusual_flow(flow_input)
        self.assertEqual(res.status, GuardStatus.WATCH)
        self.assertAlmostEqual(res.dollar_volume, 5_250_000.0)

    def test_strike_out_of_window_rejected(self):
        from src.leaps_scanner.strategies.unusual_flow import (
            evaluate_unusual_flow,
            UnusualFlowInput
        )

        # Strike 140 on spot 220 is 0.636S < 0.70S
        flow_input = UnusualFlowInput(
            symbol="AAPL270115C00140000",
            underlying="AAPL",
            spot=220.0,
            strike=140.0,
            dte=360.0,
            bid=85.0,
            ask=88.0,
            volume=600,
            open_interest=150,
            is_etf=False,
            liquidity_status=GuardStatus.PASS
        )
        res = evaluate_unusual_flow(flow_input)
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertTrue(any("STRIKE_OUT_OF_WINDOW" in r for r in res.reasons))


if __name__ == "__main__":
    unittest.main()
