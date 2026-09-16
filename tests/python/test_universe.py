import unittest
from tests.python.conftest import block_network


class TestUniverseExpansion(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_sp100_and_nasdaq100_membership(self):
        from src.leaps_scanner.data.universe import (
            SP100_COMPONENTS,
            NASDAQ100_COMPONENTS,
            CORE_ETFS,
            FULL_CORE_UNIVERSE,
            get_universe,
            is_in_core_universe
        )

        # 1. S&P 100 contains >= 100 components
        self.assertGreaterEqual(len(SP100_COMPONENTS), 100)
        self.assertIn("JPM", SP100_COMPONENTS)
        self.assertIn("UNH", SP100_COMPONENTS)
        self.assertIn("XOM", SP100_COMPONENTS)

        # 2. Nasdaq 100 contains >= 100 components
        self.assertGreaterEqual(len(NASDAQ100_COMPONENTS), 100)
        self.assertIn("NVDA", NASDAQ100_COMPONENTS)
        self.assertIn("CRWD", NASDAQ100_COMPONENTS)
        self.assertIn("ASML", NASDAQ100_COMPONENTS)

        # 3. Core ETFs
        self.assertIn("SPY", CORE_ETFS)
        self.assertIn("QQQ", CORE_ETFS)
        self.assertIn("SMH", CORE_ETFS)

        # 4. Total unique universe >= 140
        self.assertGreaterEqual(len(FULL_CORE_UNIVERSE), 140)

        # 5. Helper queries
        self.assertTrue(is_in_core_universe("AAPL"))
        self.assertTrue(is_in_core_universe("CRWD"))
        self.assertTrue(is_in_core_universe("JPM"))
        self.assertTrue(is_in_core_universe("SPY"))
        self.assertFalse(is_in_core_universe("PENNY_STOCK_XYZ"))

        # 6. Categories
        sp_list = get_universe("sp100")
        self.assertEqual(len(sp_list), len(SP100_COMPONENTS))
        nasdaq_list = get_universe("nasdaq100")
        self.assertEqual(len(nasdaq_list), len(NASDAQ100_COMPONENTS))
        all_list = get_universe("all")
        self.assertEqual(len(all_list), len(FULL_CORE_UNIVERSE))

    def test_djia_membership_and_symbology(self):
        from src.leaps_scanner.data.universe import (
            DJIA_COMPONENTS,
            FULL_CORE_UNIVERSE,
            SymbologyNormalizer,
            get_universe,
            is_in_core_universe
        )

        # 1. Exactly 30 DJIA components
        self.assertEqual(len(DJIA_COMPONENTS), 30)
        self.assertIn("NVDA", DJIA_COMPONENTS)  # Replaced INTC Nov 2024
        self.assertIn("SHW", DJIA_COMPONENTS)   # Replaced DOW Nov 2024
        self.assertIn("AAPL", DJIA_COMPONENTS)
        self.assertIn("MSFT", DJIA_COMPONENTS)
        self.assertIn("TRV", DJIA_COMPONENTS)

        # 2. Category retrieval
        djia_list = get_universe("djia")
        self.assertEqual(len(djia_list), 30)
        self.assertEqual(djia_list, sorted(DJIA_COMPONENTS))

        # 3. DJIA additions (SHW, TRV) are in FULL_CORE_UNIVERSE
        self.assertTrue(is_in_core_universe("SHW"))
        self.assertTrue(is_in_core_universe("TRV"))

        # 4. Symbology Normalizer
        self.assertEqual(SymbologyNormalizer.to_canonical("BRK.B"), "BRK.B")
        self.assertEqual(SymbologyNormalizer.to_canonical("BRK-B"), "BRK.B")
        self.assertEqual(SymbologyNormalizer.to_canonical("BRK/B"), "BRK.B")
        self.assertEqual(SymbologyNormalizer.to_canonical("bf.b"), "BF.B")
        self.assertEqual(SymbologyNormalizer.to_canonical("  aapl  "), "AAPL")

        # 5. Outbound broker conversion (Webull requires hyphen for multi-class)
        self.assertEqual(SymbologyNormalizer.to_broker("BRK.B", broker="webull"), "BRK-B")
        self.assertEqual(SymbologyNormalizer.to_broker("BF.B", broker="webull"), "BF-B")
        self.assertEqual(SymbologyNormalizer.to_broker("AAPL", broker="webull"), "AAPL")

        # 6. WebullClient outbound query contract
        from src.leaps_scanner.data.webull import WebullClient
        client = WebullClient(offline_mode=True)
        res = client.query_options_chain("BRK.B")
        self.assertEqual(res["underlying"], "BRK-B")

    def test_oversold_strategy_accepts_expanded_universe(self):
        from src.leaps_scanner.strategies.oversold import (
            evaluate_oversold_underlying,
            OversoldUnderlyingMetrics
        )
        from src.leaps_scanner.strategies.guards import GuardStatus

        # CRWD from Nasdaq 100
        metrics_crwd = OversoldUnderlyingMetrics(
            symbol="CRWD",
            spot=280.0,
            rsi_14=25.0,
            pct_to_200dma=-0.12,
            drawdown_52w_high=0.20,
            bounce_52w_low=0.05,
            hv_20=0.35,
            bar_count=250,
            is_valid=True
        )
        res_crwd = evaluate_oversold_underlying(metrics_crwd)
        self.assertEqual(res_crwd.status, GuardStatus.PASS)
        self.assertNotIn("NOT_IN_CORE_UNIVERSE", res_crwd.reasons)

        # JPM from S&P 100 / DJIA
        metrics_jpm = OversoldUnderlyingMetrics(
            symbol="JPM",
            spot=210.0,
            rsi_14=27.0,
            pct_to_200dma=-0.11,
            drawdown_52w_high=0.16,
            bounce_52w_low=0.04,
            hv_20=0.22,
            bar_count=250,
            is_valid=True
        )
        res_jpm = evaluate_oversold_underlying(metrics_jpm)
        self.assertEqual(res_jpm.status, GuardStatus.PASS)


if __name__ == "__main__":
    unittest.main()

