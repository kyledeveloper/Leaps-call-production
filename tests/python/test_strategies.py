import unittest
from tests.python.conftest import block_network

class TestStrategies(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_strategy_one_pass_case(self):
        from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
        from src.leaps_scanner.strategies.guards import GuardStatus
        # Contract with Delta = 0.80 (Pass)
        # S = 100.0, K = 70.0 (Intrinsic = 30.0)
        # P_exec = 33.0 (Intrinsic ratio = 30/33 = 90.9% >= 80% Pass)
        # Leverage = 0.80 * 100 / 33 = 2.42 -> Watch/Pass
        # DTE = 365.25 (T = 1.0) -> Carry = (3/33) + 0.01 = 9.1% + 1% = 10.1% -> Watch
        # Let's craft a pristine Pass candidate:
        # S = 200.0, K = 120.0 (Intrinsic = 80.0)
        # P_exec = 82.5 (Intrinsic ratio = 80 / 82.5 = 96.9% Pass)
        # Delta = 0.82 (Pass)
        # Leverage = 0.82 * 200 / 82.5 = 1.98 -> Let's adjust S=200, K=135
        # Intrinsic = 65, P_exec = 67.0
        # Intrinsic ratio = 65 / 67 = 97.0% Pass
        # Delta = 0.80
        # Leverage = 0.80 * 200 / 67.0 = 2.38
        # Let's check S=100, K=65, P_exec=37.0, Delta=0.82, DTE=400, q=0.01
        # Intrinsic = 35.0, Extrinsic = 2.0
        # Intrinsic ratio = 35 / 37 = 94.6% Pass
        # Leverage = 0.82 * 100 / 37 = 2.21
        # Carry = (2.0 / (37 * (400/365.25))) + 0.01 = 4.9% + 1% = 5.9% (Watch)
        # Carry < 5% Pass: Extrinsic = 1.0, P_exec = 36.0 -> 1/(36*1.1) = 2.5% + 1% = 3.5% Pass!
        # Leverage: Delta * 100 / 36 = 0.80 * 100 / 36 = 2.22 (Let's check target leverage [2.5, 4.5])
        # If Delta=0.80, Leverage = 2.8 -> P_exec = 0.80 * 100 / 2.8 = 28.5. K = 73.
        res = evaluate_deep_itm(
            spot=100.0,
            strike=73.0,
            dte=400.0,
            p_exec=27.8,
            delta=0.80,
            dividend_yield=0.01,
            iv=None  # NOTE: IV is unavailable, must NOT reject!
        )
        self.assertEqual(res.status, GuardStatus.PASS)
        self.assertAlmostEqual(res.effective_leverage, 2.877, places=2)
        self.assertLess(res.carry_cost, 0.15)

    def test_strategy_one_carry_tiers(self):
        from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
        from src.leaps_scanner.strategies.guards import GuardStatus

        excellent = evaluate_deep_itm(
            spot=100.0, strike=73.0, dte=400.0, p_exec=27.8, delta=0.80, dividend_yield=0.03
        )
        self.assertEqual(excellent.status, GuardStatus.PASS)
        self.assertLess(excellent.carry_cost, 0.15)

        # DTE=250, P=33.3, K=75 → ~36% time-value drag, should REJECT on carry
        heavy = evaluate_deep_itm(
            spot=100.0, strike=75.0, dte=250.0, p_exec=33.3, delta=0.80, dividend_yield=0.0
        )
        self.assertGreaterEqual(heavy.carry_cost, 0.35)
        self.assertEqual(heavy.status, GuardStatus.REJECT)
        self.assertTrue(any(r.startswith("HIGH_CARRY_DRAG_") for r in heavy.reasons))

        # ~20% drag: acceptable WATCH, not reject (q must not be added)
        mid = evaluate_deep_itm(
            spot=100.0, strike=76.0, dte=400.0, p_exec=30.0, delta=0.80, dividend_yield=0.04
        )
        self.assertGreaterEqual(mid.carry_cost, 0.15)
        self.assertLess(mid.carry_cost, 0.25)
        self.assertNotEqual(mid.status, GuardStatus.REJECT)

    def test_strategy_one_promoted_delta_and_strike_window(self):
        from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
        from src.leaps_scanner.strategies.guards import GuardStatus

        # 0.72 delta is now PASS, not WATCH
        res = evaluate_deep_itm(
            spot=100.0, strike=73.0, dte=400.0, p_exec=27.8, delta=0.72, dividend_yield=0.0
        )
        self.assertEqual(res.status, GuardStatus.PASS)

        # 0.58S is outside [0.65, 0.85]
        too_deep = evaluate_deep_itm(
            spot=100.0, strike=58.0, dte=400.0, p_exec=44.0, delta=0.80, dividend_yield=0.0
        )
        self.assertEqual(too_deep.status, GuardStatus.REJECT)
        self.assertTrue(any(r.startswith("STRIKE_OUT_OF_WINDOW_") for r in too_deep.reasons))

        # Intrinsic 60% is WATCH, not REJECT
        mid_intrinsic = evaluate_deep_itm(
            spot=100.0, strike=80.0, dte=400.0, p_exec=33.0, delta=0.78, dividend_yield=0.0
        )
        self.assertGreaterEqual(mid_intrinsic.intrinsic_ratio, 0.55)
        self.assertLess(mid_intrinsic.intrinsic_ratio, 0.65)

    def test_strategy_one_liquidity_zero_bid(self):
        from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
        from src.leaps_scanner.strategies.guards import GuardStatus
        res = evaluate_deep_itm(
            spot=100.0, strike=73.0, dte=400.0, p_exec=27.8, delta=0.80,
            bid=0.0, ask=28.0, open_interest=500, volume=80, ask_size=20,
        )
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertTrue(any("ZERO_BID" in r for r in res.reasons))

    def test_strategy_one_runs_when_iv_unavailable(self):
        from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
        # Defensive Clause 4 / Global rule: Deep ITM Vega ~ 0, IV None allowed!
        res = evaluate_deep_itm(
            spot=100.0,
            strike=73.0,
            dte=400.0,
            p_exec=28.5,
            delta=0.80,
            dividend_yield=0.01,
            iv=None
        )
        self.assertNotEqual(res.status, "REJECT")
        self.assertFalse(res.is_rejected)

    def test_strategy_one_rejects_out_of_bounds_delta(self):
        from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
        from src.leaps_scanner.strategies.guards import GuardStatus
        # Delta = 0.65 (< 0.70 Reject)
        res = evaluate_deep_itm(
            spot=100.0,
            strike=85.0,
            dte=400.0,
            p_exec=18.0,
            delta=0.65,
            dividend_yield=0.01
        )
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertIn("DELTA_OUT_OF_BOUNDS", res.reasons)

    def test_strategy_one_daily_theta_tiers(self):
        from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
        from src.leaps_scanner.strategies.guards import GuardStatus

        none_iv = evaluate_deep_itm(
            spot=100.0, strike=73.0, dte=400.0, p_exec=27.8, delta=0.80, iv=None
        )
        self.assertEqual(none_iv.status, GuardStatus.PASS)
        self.assertEqual(none_iv.theta_daily_pct, 0.0)

        mild = evaluate_deep_itm(
            spot=100.0, strike=73.0, dte=400.0, p_exec=27.8, delta=0.80, iv=0.22
        )
        self.assertGreater(mild.theta_daily_pct, 0.0)
        self.assertLess(mild.theta_daily_pct, 0.0008)
        self.assertNotEqual(mild.status, GuardStatus.REJECT)

        hot = evaluate_deep_itm(
            spot=100.0, strike=85.0, dte=250.0, p_exec=22.0, delta=0.80, iv=0.60
        )
        self.assertGreaterEqual(hot.theta_daily_pct, 0.0010)
        self.assertEqual(hot.status, GuardStatus.REJECT)
        self.assertTrue(any(r.startswith("HIGH_THETA_DRAG_") for r in hot.reasons))

if __name__ == "__main__":
    unittest.main()
