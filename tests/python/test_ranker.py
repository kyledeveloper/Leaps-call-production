import time
import unittest
from tests.python.conftest import block_network

class TestRankerAndMemoryReordering(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_pure_memory_alpha_reranking(self):
        from src.leaps_scanner.scoring.ranker import StrategyCandidate, MemoryRanker
        # Construct two candidate records
        c1 = StrategyCandidate(
            symbol="AAPL270115C00150000",
            underlying="AAPL",
            strike=150.0,
            spot=220.0,
            dte=480.0,
            bid=75.0,
            ask=78.0,
            delta=0.82,
            dividend_yield=0.005,
            open_interest=1500,
            volume=80,
            strategy_name="deep_itm"
        )
        c2 = StrategyCandidate(
            symbol="AAPL270115C00140000",
            underlying="AAPL",
            strike=140.0,
            spot=220.0,
            dte=480.0,
            bid=85.0,
            ask=88.5,
            delta=0.86,
            dividend_yield=0.005,
            open_interest=2200,
            volume=120,
            strategy_name="deep_itm"
        )

        ranker = MemoryRanker(candidates=[c1, c2])

        # Test initial ranking with alpha=0.5
        board_alpha_05 = ranker.rank(alpha=0.5)
        self.assertEqual(len(board_alpha_05), 2)
        # 150/220=0.68 is inside [0.65, 0.85]; 140/220=0.64 is prepaid-equity REJECT
        self.assertEqual(board_alpha_05[0].symbol, "AAPL270115C00150000")
        self.assertEqual(board_alpha_05[1].symbol, "AAPL270115C00140000")

        items_05 = {x.symbol: x for x in board_alpha_05}
        # Check P_exec for c1: Mid=76.5, half_spread=1.5 -> P_exec = 76.5 + 0.5 * 1.5 = 77.25
        self.assertAlmostEqual(items_05["AAPL270115C00150000"].p_exec, 77.25, places=2)
        self.assertAlmostEqual(items_05["AAPL270115C00140000"].p_exec, 87.625, places=2)

        # Test instant memory re-ranking with alpha=1.0 (Conservative Ask)
        t_start = time.perf_counter()
        board_alpha_10 = ranker.rank(alpha=1.0)
        elapsed = (time.perf_counter() - t_start) * 1000.0  # in ms

        items_10 = {x.symbol: x for x in board_alpha_10}
        # Check P_exec for c1 with alpha=1.0: P_exec = Ask = 78.0
        self.assertAlmostEqual(items_10["AAPL270115C00150000"].p_exec, 78.0, places=2)
        self.assertAlmostEqual(items_10["AAPL270115C00140000"].p_exec, 88.5, places=2)

    def test_rank_boards_multi_strategy(self):
        from src.leaps_scanner.scoring.ranker import StrategyCandidate, MemoryRanker
        c = StrategyCandidate(
            symbol="AAPL270115C00200000",
            underlying="AAPL",
            strike=200.0,
            spot=220.0,
            dte=360.0,
            bid=32.0,
            ask=34.0,
            delta=0.65,
            open_interest=500,
            volume=2000,
            iv=0.22,
            iv_percentile=0.15,
            iv_rank=0.15,
            iv_z_score=-1.1,
            rsi_14=28.0,
            pct_to_200dma=-0.12,
            drawdown_52w_high=0.18,
            bounce_52w_low=0.04,
            valid_history_days=250
        )
        ranker = MemoryRanker(candidates=[c])
        boards = ranker.rank_boards(alpha=0.5)

        self.assertIn("deep_itm", boards)
        self.assertIn("vol_discount", boards)
        self.assertIn("oversold", boards)
        self.assertNotIn("unusual_flow", boards)

        # In vol_discount board, AAPL should pass Regime A
        self.assertEqual(len(boards["vol_discount"]), 1)
        self.assertEqual(boards["vol_discount"][0].strategy_name, "vol_discount")
        self.assertEqual(boards["vol_discount"][0].regime, "REGIME_A")

        # In oversold board, AAPL should pass with high confluence
        self.assertEqual(len(boards["oversold"]), 1)
        self.assertEqual(boards["oversold"][0].strategy_name, "oversold")
        self.assertGreaterEqual(boards["oversold"][0].confluence_score, 2.0)

if __name__ == "__main__":
    unittest.main()
