import unittest
from tests.python.conftest import block_network
from src.leaps_scanner.strategies.guards import GuardStatus


class TestFinancialEdgeCases(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_bs2002_microscopic_dividend_preserves_extrinsic(self):
        from src.leaps_scanner.core.american_pricing import bjerksund_stensland_2002, black_scholes_call

        spot = 120.0
        strike = 100.0
        t = 1.5
        r = 0.05
        q = 1e-6
        sigma = 0.25

        eur_price = black_scholes_call(spot, strike, t, r, q, sigma)
        ame_price = bjerksund_stensland_2002(spot, strike, t, r, q, sigma)

        self.assertGreater(ame_price, 20.0)
        self.assertAlmostEqual(ame_price, eur_price, places=3)

    def test_bs2002_numerical_overflow_dampening(self):
        from src.leaps_scanner.core.american_pricing import bjerksund_stensland_2002

        price = bjerksund_stensland_2002(
            spot=100.0,
            strike=80.0,
            t=5.0,
            r=0.01,
            q=0.15,
            sigma=0.05
        )
        self.assertIsInstance(price, float)
        self.assertGreaterEqual(price, 20.0)
        self.assertLessEqual(price, 100.0)

    def test_iv_solver_zero_extrinsic_returns_none(self):
        from src.leaps_scanner.core.iv_solver import solve_implied_volatility

        spot = 200.0
        strike = 50.0
        t = 2.0
        price = 150.0

        res = solve_implied_volatility(
            price=price,
            spot=spot,
            strike=strike,
            t=t,
            r=0.04,
            q=0.02
        )
        self.assertEqual(res.status, 'ZERO_EXTRINSIC')
        self.assertIsNone(res.iv)

    def test_greeks_theta_daily_property(self):
        from src.leaps_scanner.core.greeks import calculate_american_greeks

        greeks = calculate_american_greeks(
            spot=100.0,
            strike=85.0,
            t=1.0,
            r=0.04,
            q=0.015,
            sigma=0.25
        )
        self.assertLess(greeks.theta, 0.0)
        self.assertAlmostEqual(greeks.theta_daily, greeks.theta / 365.25, places=6)

    def test_ranker_vol_discount_board_parity_on_missing_iv(self):
        from src.leaps_scanner.scoring.ranker import MemoryRanker, StrategyCandidate

        cand_no_iv = StrategyCandidate(
            symbol='AAPL270115C00050000',
            underlying='AAPL',
            strike=50.0,
            spot=200.0,
            dte=360.0,
            bid=149.0,
            ask=151.0,
            delta=0.82,
            open_interest=500,
            volume=100,
            iv=None,
            iv_percentile=None
        )

        ranker = MemoryRanker([cand_no_iv])
        boards = ranker.rank_boards()

        self.assertEqual(len(boards['deep_itm']), 1)
        self.assertEqual(len(boards['oversold']), 1)
        self.assertEqual(len(boards['vol_discount']), 1)
        self.assertNotIn('unusual_flow', boards)
        item = boards['vol_discount'][0]
        self.assertEqual(item.status, GuardStatus.REJECT)
        self.assertTrue(any('IV_UNAVAILABLE' in r or 'MISSING_IV' in r for r in item.reasons))


if __name__ == '__main__':
    unittest.main()
