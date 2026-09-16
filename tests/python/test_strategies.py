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
        self.assertLess(res.carry_cost, 0.05)

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

if __name__ == "__main__":
    unittest.main()
