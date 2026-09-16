import os
import json
import tempfile
import unittest
from pathlib import Path
from tests.python.conftest import block_network


class TestDynamicRebalancer(unittest.TestCase):
    def setUp(self):
        block_network()
        self.fixtures_dir = Path(__file__).parent / "fixtures"

    def test_wikipedia_parser_offline_fixtures(self):
        from src.leaps_scanner.data.rebalancer import WikipediaConstituentFetcher

        fetcher = WikipediaConstituentFetcher(offline_mode=True)

        # 1. Parse DJIA offline fixture
        djia_path = self.fixtures_dir / "wiki_djia.html"
        self.assertTrue(djia_path.exists())
        with open(djia_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        djia_symbols = fetcher.parse_html_table(html_content, index_name="djia")
        self.assertEqual(len(djia_symbols), 30)
        self.assertIn("NVDA", djia_symbols)  # footnote stripped
        self.assertIn("CRM", djia_symbols)   # special char stripped
        self.assertIn("UNH", djia_symbols)   # NYSE prefix stripped
        self.assertIn("SHW", djia_symbols)
        self.assertIn("TRV", djia_symbols)
        self.assertIn("AAPL", djia_symbols)

        # 2. Parse SP100 offline fixture
        sp100_path = self.fixtures_dir / "wiki_sp100.html"
        self.assertTrue(sp100_path.exists())
        with open(sp100_path, "r", encoding="utf-8") as f:
            html_content_sp = f.read()

        sp100_symbols = fetcher.parse_html_table(html_content_sp, index_name="sp100")
        self.assertGreaterEqual(len(sp100_symbols), 100)
        self.assertIn("BRK.B", sp100_symbols)  # dot notation canonical
        self.assertIn("JPM", sp100_symbols)

    def test_cardinality_gatekeeper_rejections(self):
        from src.leaps_scanner.data.rebalancer import WikipediaConstituentFetcher, RebalanceValidationError

        fetcher = WikipediaConstituentFetcher(offline_mode=True)

        # Truncated DJIA table (< 30)
        truncated_html = """
        <table class="wikitable">
          <thead><tr><th>Symbol</th><th>Company</th></tr></thead>
          <tbody>
            <tr><td>AAPL</td><td>Apple</td></tr>
            <tr><td>MSFT</td><td>Microsoft</td></tr>
          </tbody>
        </table>
        """
        with self.assertRaises(RebalanceValidationError):
            fetcher.parse_html_table(truncated_html, index_name="djia")

        # Exceeded DJIA table (> 30)
        excess_rows = "".join(f"<tr><td>SYM{i}</td><td>Company {i}</td></tr>" for i in range(35))
        excess_html = f"""
        <table class="wikitable">
          <thead><tr><th>Symbol</th><th>Company</th></tr></thead>
          <tbody>{excess_rows}</tbody>
        </table>
        """
        with self.assertRaises(RebalanceValidationError):
            fetcher.parse_html_table(excess_html, index_name="djia")

    def test_dynamic_rebalance_detection_and_multi_index_union(self):
        from src.leaps_scanner.data.rebalancer import DynamicUniverseManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = os.path.join(tmp_dir, "universe_cache.json")
            manager = DynamicUniverseManager(cache_path=cache_file, offline_mode=True)

            # Initial state: seed constituents loaded
            self.assertEqual(len(manager.get_constituents("djia")), 30)
            self.assertIn("NVDA", manager.get_constituents("djia"))
            self.assertIn("SHW", manager.get_constituents("djia"))

            # Simulate an index rebalance event on DJIA:
            # Suppose INTC replaces SHW in DJIA
            simulated_djia = [s for s in manager.get_constituents("djia") if s != "SHW"] + ["INTC"]
            self.assertEqual(len(simulated_djia), 30)

            diff = manager.apply_rebalance(
                index_name="djia",
                new_constituents=simulated_djia,
                reason="Simulated committee rebalance"
            )

            self.assertEqual(diff["added"], ["INTC"])
            self.assertEqual(diff["removed"], ["SHW"])

            # Verify master universe:
            # INTC was in SP100 & NDX100 anyway, so it remains in master.
            # SHW was ONLY in DJIA, so removing it drops it from master (unless pinned).
            master_universe = manager.get_master_universe()
            self.assertIn("INTC", master_universe)
            self.assertNotIn("SHW", master_universe)

            # Rebalance history logged
            history = manager.get_rebalance_history()
            self.assertGreaterEqual(len(history), 1)
            self.assertEqual(history[-1]["index"], "djia")
            self.assertEqual(history[-1]["added"], ["INTC"])
            self.assertEqual(history[-1]["removed"], ["SHW"])

    def test_corrupted_cache_quarantine_and_self_healing(self):
        from src.leaps_scanner.data.rebalancer import DynamicUniverseManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = os.path.join(tmp_dir, "universe_cache.json")
            
            # Write garbage corrupted data
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write("{ invalid json corrupted content !@#$")

            # Manager should catch corruption, quarantine to .corrupt.bak, and boot cleanly from seed
            manager = DynamicUniverseManager(cache_path=cache_file, offline_mode=True)
            self.assertEqual(len(manager.get_constituents("djia")), 30)
            self.assertGreaterEqual(len(manager.get_master_universe()), 140)

    def test_pinned_symbols_and_grace_period_retention(self):
        from src.leaps_scanner.data.rebalancer import DynamicUniverseManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = os.path.join(tmp_dir, "universe_cache.json")
            # Pin SHW (as if user holds an active LEAPS contract)
            manager = DynamicUniverseManager(
                cache_path=cache_file,
                offline_mode=True,
                pinned_symbols={"SHW"}
            )

            # Remove SHW from DJIA
            simulated_djia = [s for s in manager.get_constituents("djia") if s != "SHW"] + ["INTC"]
            diff = manager.apply_rebalance("djia", simulated_djia)
            self.assertIn("SHW", diff["removed"])

            # Verify SHW is safely retained in master universe under REMOVED_GRACE_PERIOD!
            master = manager.get_master_universe()
            self.assertIn("SHW", master)


if __name__ == "__main__":
    unittest.main()
