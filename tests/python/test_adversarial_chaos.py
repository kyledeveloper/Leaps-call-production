import socket
import unittest
from tests.python.conftest import block_network
from src.leaps_scanner.core.american_pricing import bjerksund_stensland_2002
from src.leaps_scanner.core.iv_solver import solve_implied_volatility
from src.leaps_scanner.core.metrics import calculate_pexec, calculate_carry_cost
from src.leaps_scanner.strategies.guards import evaluate_liquidity_guard, GuardStatus
from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
from src.leaps_scanner.strategies.oversold import evaluate_oversold_underlying, OversoldUnderlyingMetrics


class TestAdversarialChaos(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_network_isolation_breach_prevention(self):
        """Verify that any accidental socket network call is strictly intercepted."""
        s = socket.socket()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                s.connect(("api.webull.com", 443))
            self.assertIn("blocked by Hermetic Test Guard", str(ctx.exception))
        finally:
            s.close()

    def test_arbitrage_inversion_and_negative_prices(self):
        """Pricing engine and IV solver must handle C > S or inverted quotes defensively."""
        # 1. Option price exceeds underlying spot (C > S)
        res_iv = solve_implied_volatility(
            price=250.0,
            spot=200.0,
            strike=150.0,
            t=1.0,
            r=0.04,
            q=0.0
        )
        self.assertIsNone(res_iv.iv)
        self.assertEqual(res_iv.status, "ARBITRAGE_VIOLATION")

        # 2. Inverted bid-ask quotes
        with self.assertRaises(ValueError):
            calculate_pexec(bid=80.0, ask=70.0, alpha=0.5)

    def test_post_market_zero_bid_chaos(self):
        """Options with bid=0 must be classified as REJECT with POST_MARKET_ZERO_BID."""
        guard_res = evaluate_liquidity_guard(
            bid=0.0,
            ask=5.0,
            open_interest=500,
            volume=50,
            is_rth=False
        )
        self.assertEqual(guard_res.status, GuardStatus.REJECT)
        self.assertIn("ZERO_BID_POST_MARKET", guard_res.reasons)

    def test_missing_iv_resilience(self):
        """Strategy 1 Deep ITM and Strategy 3 Oversold must evaluate normally even if IV is None."""
        strat_res = evaluate_deep_itm(
            spot=220.0,
            strike=150.0,
            dte=480.0,
            p_exec=77.25,
            delta=0.82,
            dividend_yield=0.005,
            iv=None  # IV is None
        )
        self.assertIn(strat_res.status, (GuardStatus.PASS, GuardStatus.WATCH))
        self.assertGreater(strat_res.intrinsic_ratio, 0.8)

    def test_non_standard_multiplier_rejection(self):
        """Non-standard multiplier contracts must be filtered."""
        guard_res = evaluate_liquidity_guard(
            bid=10.0,
            ask=12.0,
            open_interest=500,
            volume=100,
            multiplier=50
        )
        self.assertEqual(guard_res.status, GuardStatus.REJECT)
        self.assertIn("NON_STANDARD", guard_res.reasons)

    def test_extreme_alpha_boundaries(self):
        """Alpha parameter boundaries 0.0 (Mid), 1.0 (Ask), and elevated size adjustments."""
        # Alpha = 0.0 -> P_exec = Mid
        p0 = calculate_pexec(bid=10.0, ask=12.0, alpha=0.0)
        self.assertAlmostEqual(p0.p_exec, 11.0)

        # Alpha = 1.0 -> P_exec = Ask
        p1 = calculate_pexec(bid=10.0, ask=12.0, alpha=1.0)
        self.assertAlmostEqual(p1.p_exec, 12.0)

        # Thin book ask_size < target_contracts forces alpha to max(alpha, 0.75)
        p_thin = calculate_pexec(bid=10.0, ask=12.0, ask_size=2, target_contracts=5, alpha=0.0)
        self.assertTrue(p_thin.alpha_elevated)
        self.assertAlmostEqual(p_thin.effective_alpha, 0.75)
        self.assertAlmostEqual(p_thin.p_exec, 11.75)


if __name__ == "__main__":
    unittest.main()
