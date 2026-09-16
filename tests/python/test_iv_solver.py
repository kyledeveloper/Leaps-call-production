import unittest
from tests.python.conftest import block_network

class TestIVSolver(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_iv_solver_convergence(self):
        from src.leaps_scanner.core.american_pricing import bjerksund_stensland_2002
        from src.leaps_scanner.core.iv_solver import solve_implied_volatility
        # S=100, K=90, T=1.0, r=0.04, q=0.02, known true sigma = 0.28
        true_sigma = 0.28
        target_price = bjerksund_stensland_2002(100.0, 90.0, 1.0, 0.04, 0.02, true_sigma)

        res = solve_implied_volatility(
            price=target_price,
            spot=100.0,
            strike=90.0,
            t=1.0,
            r=0.04,
            q=0.02
        )
        self.assertEqual(res.status, "CONVERGED")
        self.assertIsNotNone(res.iv)
        self.assertAlmostEqual(res.iv, true_sigma, places=3)

    def test_arbitrage_violation_detection(self):
        from src.leaps_scanner.core.iv_solver import solve_implied_volatility
        # Price < Intrinsic: S=100, K=70 (Intrinsic=30), but market price=25
        res_below = solve_implied_volatility(
            price=25.0, spot=100.0, strike=70.0, t=1.0, r=0.04, q=0.02
        )
        self.assertEqual(res_below.status, "ARBITRAGE_VIOLATION")
        self.assertIsNone(res_below.iv)

        # Price > Spot: S=100, K=70, market price=105 > S (violates American upper bound C <= S)
        res_above = solve_implied_volatility(
            price=105.0, spot=100.0, strike=70.0, t=1.0, r=0.04, q=0.02
        )
        self.assertEqual(res_above.status, "ARBITRAGE_VIOLATION")
        self.assertIsNone(res_above.iv)

    def test_deep_itm_graceful_degradation(self):
        from src.leaps_scanner.core.iv_solver import solve_implied_volatility
        # Deep ITM with Vega ~ 0 where price = intrinsic
        # S=200, K=50, T=2.0, price = 150.0 (zero extrinsic value)
        res = solve_implied_volatility(
            price=150.0, spot=200.0, strike=50.0, t=2.0, r=0.04, q=0.02
        )
        # Should gracefully return IV_UNAVAILABLE / ZERO_EXTRINSIC without throwing exception
        self.assertIn(res.status, ["IV_UNAVAILABLE", "ZERO_EXTRINSIC", "CONVERGED"])

if __name__ == "__main__":
    unittest.main()
