import json
import os
import unittest
from tests.python.conftest import block_network

class TestAmericanPricing(unittest.TestCase):
    def setUp(self):
        block_network()
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "bs2002_goldens.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            self.goldens = json.load(f)

    def test_bs2002_goldens_alignment(self):
        from src.leaps_scanner.core.american_pricing import bjerksund_stensland_2002
        for case in self.goldens:
            price = bjerksund_stensland_2002(
                spot=case["spot"],
                strike=case["strike"],
                t=case["t"],
                r=case["r"],
                q=case["q"],
                sigma=case["sigma"]
            )
            self.assertAlmostEqual(
                price,
                case["expected_price"],
                delta=case["tolerance"],
                msg=f"Failed on: {case['description']}"
            )

    def test_european_lower_bound_and_no_dividend_equality(self):
        from src.leaps_scanner.core.american_pricing import bjerksund_stensland_2002, black_scholes_call
        # Without dividend, American call must equal European call
        spot = 150.0
        strike = 130.0
        t = 1.5
        r = 0.045
        q = 0.0
        sigma = 0.28

        eur_price = black_scholes_call(spot=spot, strike=strike, t=t, r=r, q=q, sigma=sigma)
        ame_price = bjerksund_stensland_2002(spot=spot, strike=strike, t=t, r=r, q=q, sigma=sigma)

        # Without dividend, early exercise is never optimal
        self.assertAlmostEqual(ame_price, eur_price, places=3)
        self.assertGreaterEqual(ame_price, max(0.0, spot - strike))

    def test_dividend_paying_early_exercise_premium(self):
        from src.leaps_scanner.core.american_pricing import bjerksund_stensland_2002, black_scholes_call
        # With high dividend yield (e.g. 6%), American Call >= European Call
        spot = 100.0
        strike = 80.0
        t = 1.5
        r = 0.05
        q = 0.06
        sigma = 0.22

        eur_price = black_scholes_call(spot=spot, strike=strike, t=t, r=r, q=q, sigma=sigma)
        ame_price = bjerksund_stensland_2002(spot=spot, strike=strike, t=t, r=r, q=q, sigma=sigma)

        # American option has non-negative early exercise premium
        self.assertGreaterEqual(ame_price, eur_price - 1e-6)
        # Must respect American upper bound C <= S
        self.assertLessEqual(ame_price, spot)

    def test_numerical_greeks_consistency(self):
        from src.leaps_scanner.core.greeks import calculate_american_greeks
        greeks = calculate_american_greeks(
            spot=100.0,
            strike=85.0,
            t=1.0,
            r=0.04,
            q=0.015,
            sigma=0.25
        )
        # Deep ITM Delta should be between 0.70 and 0.90
        self.assertGreater(greeks.delta, 0.70)
        self.assertLess(greeks.delta, 0.90)
        self.assertGreater(greeks.gamma, 0.0)
        self.assertGreater(greeks.vega, 0.0)
        # Call theta is typically negative
        self.assertLess(greeks.theta, 0.0)

if __name__ == "__main__":
    unittest.main()
