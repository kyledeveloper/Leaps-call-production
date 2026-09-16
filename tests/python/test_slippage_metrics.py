import unittest
from tests.python.conftest import block_network

class TestSlippageMetrics(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_pexec_standard_calculation(self):
        from src.leaps_scanner.core.metrics import calculate_pexec
        # Bid=5.0, Ask=8.0, Mid=6.5, half-spread=1.5
        # Default alpha=0.5, displayed size=10 >= target 5
        res = calculate_pexec(bid=5.0, ask=8.0, ask_size=10, alpha=0.5, target_contracts=5)
        self.assertAlmostEqual(res.p_exec, 7.25, places=4)
        self.assertAlmostEqual(res.mid, 6.5, places=4)
        self.assertAlmostEqual(res.half_spread, 1.5, places=4)
        self.assertFalse(res.alpha_elevated)

    def test_pexec_small_size_elevates_alpha(self):
        from src.leaps_scanner.core.metrics import calculate_pexec
        # Ask size=1 < target 5: alpha elevated to min 0.75
        res = calculate_pexec(bid=5.0, ask=8.0, ask_size=1, alpha=0.5, target_contracts=5)
        self.assertTrue(res.alpha_elevated)
        self.assertAlmostEqual(res.effective_alpha, 0.75, places=4)
        # 6.5 + 0.75 * 1.5 = 7.625
        self.assertAlmostEqual(res.p_exec, 7.625, places=4)

    def test_round_trip_cost(self):
        from src.leaps_scanner.core.metrics import calculate_round_trip
        rt = calculate_round_trip(bid=5.0, ask=8.0, alpha=0.5)
        self.assertAlmostEqual(rt.p_exec, 7.25, places=4)
        self.assertAlmostEqual(rt.p_sell, 5.75, places=4)
        self.assertAlmostEqual(rt.round_trip_per_share, 1.50, places=4)
        self.assertAlmostEqual(rt.round_trip_per_contract, 150.0, places=2)

    def test_comprehensive_carry_with_dividend(self):
        from src.leaps_scanner.core.metrics import calculate_carry_cost
        # S=100, K=70, DTE=365.25 -> T=1.0, Intrinsic=(100-70)=30
        # P_exec = 32.0 -> Extrinsic = 2.0
        # q_div = 0.025 (2.5%)
        carry = calculate_carry_cost(
            spot=100.0,
            strike=70.0,
            dte=365.25,
            p_exec=32.0,
            dividend_yield=0.025
        )
        # Extrinsic decay = 2.0 / (32.0 * 1.0) = 0.0625 (6.25%)
        # Total Carry = 0.0625 + 0.025 = 0.0875 (8.75%)
        self.assertAlmostEqual(carry.intrinsic_per_share, 30.0, places=4)
        self.assertAlmostEqual(carry.extrinsic_per_share, 2.0, places=4)
        self.assertAlmostEqual(carry.annualized_extrinsic_rate, 0.0625, places=4)
        self.assertAlmostEqual(carry.total_annualized_carry, 0.0875, places=4)
        self.assertAlmostEqual(carry.dividend_yield, 0.025, places=4)

    def test_effective_leverage(self):
        from src.leaps_scanner.core.metrics import calculate_effective_leverage
        # Delta=0.80, S=100.0, P_exec=25.0 -> Leverage = 0.80 * 100 / 25 = 3.2
        lev = calculate_effective_leverage(delta=0.80, spot=100.0, p_exec=25.0)
        self.assertAlmostEqual(lev, 3.2, places=4)

if __name__ == "__main__":
    unittest.main()
