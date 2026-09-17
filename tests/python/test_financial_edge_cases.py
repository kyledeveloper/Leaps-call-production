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

    def test_ranker_strategy_one_zero_volume_high_oi_and_dividend_sorting(self):
        from src.leaps_scanner.scoring.ranker import MemoryRanker, StrategyCandidate

        # Candidate A: Low dividend (0.5%), Volume=0, deep OI=600 -> PASS
        cand_a = StrategyCandidate(
            symbol='AAPL270115C00150000',
            underlying='AAPL',
            strike=150.0,
            spot=200.0,
            dte=365.0,
            bid=53.0,
            ask=54.0,
            ask_size=10,
            delta=0.80,
            open_interest=600,
            volume=0,
            dividend_yield=0.005,
        )

        # Candidate B: High dividend (4.0%), Volume=0, deep OI=600 -> PASS, but higher carry
        cand_b = StrategyCandidate(
            symbol='KO270115C00150000',
            underlying='KO',
            strike=150.0,
            spot=200.0,
            dte=365.0,
            bid=53.0,
            ask=54.0,
            ask_size=10,
            delta=0.80,
            open_interest=600,
            volume=0,
            dividend_yield=0.04,
        )

        # Candidate C: Empty ask order book (ask_size=0) -> REJECT (Defensive Clause 7)
        cand_c = StrategyCandidate(
            symbol='BAD270115C00150000',
            underlying='BAD',
            strike=150.0,
            spot=200.0,
            dte=365.0,
            bid=53.0,
            ask=54.0,
            ask_size=0,
            delta=0.80,
            open_interest=600,
            volume=0,
            dividend_yield=0.01,
        )

        # Candidate D: NaN dividend yield -> immune to Timsort crash
        cand_d = StrategyCandidate(
            symbol='NAN270115C00150000',
            underlying='NAN',
            strike=150.0,
            spot=200.0,
            dte=365.0,
            bid=53.0,
            ask=54.0,
            ask_size=10,
            delta=0.80,
            open_interest=600,
            volume=0,
            dividend_yield=float('nan'),
        )

        ranker = MemoryRanker([cand_b, cand_c, cand_a, cand_d])
        boards = ranker.rank_boards()
        deep_items = boards['deep_itm']

        self.assertEqual(len(deep_items), 4)

        # Candidate A and Candidate B both PASS despite Volume=0!
        item_a = next(it for it in deep_items if it.underlying == 'AAPL')
        item_b = next(it for it in deep_items if it.underlying == 'KO')
        item_c = next(it for it in deep_items if it.underlying == 'BAD')
        item_d = next(it for it in deep_items if it.underlying == 'NAN')

        self.assertEqual(item_a.status, GuardStatus.PASS)
        self.assertEqual(item_b.status, GuardStatus.PASS)
        self.assertEqual(item_c.status, GuardStatus.REJECT)

        # Low dividend Candidate A has lower total carry than high dividend Candidate B
        self.assertLess(item_a.carry_cost, item_b.carry_cost)

        # Ranker sorts by (tier_order, carry_cost), so AAPL (0.5% div) ranks ahead of KO (4% div)
        pass_items = [it for it in deep_items if it.status == GuardStatus.PASS]
        pass_symbols = [it.underlying for it in pass_items]
        self.assertLess(pass_symbols.index('AAPL'), pass_symbols.index('KO'))


if __name__ == '__main__':
    unittest.main()
