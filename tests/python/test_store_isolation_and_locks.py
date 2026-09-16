import os
import tempfile
import threading
import unittest
from pathlib import Path

from tests.python.conftest import block_network
from src.leaps_scanner.api.server import AppState
from src.leaps_scanner.data.store.iv_history import IVHistoryStore, IVDataPoint
from src.leaps_scanner.data.store.daily_bars import DailyBarCache


class TestStoreIsolationAndLocks(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_app_state_offline_uses_in_memory_stores_without_disk_pollution(self):
        repo_data = (Path(__file__).resolve().parents[2] / "data").resolve()
        state = AppState(offline_mode=True)
        self.assertIsNotNone(state.iv_store._path)
        self.assertIsNotNone(state.bar_cache._path)
        iv_path = Path(state.iv_store._path).resolve()
        bar_path = Path(state.bar_cache._path).resolve()
        self.assertFalse(str(iv_path).startswith(str(repo_data)))
        self.assertFalse(str(bar_path).startswith(str(repo_data)))
        self.assertEqual(str(iv_path), str(Path(os.environ["LEAPS_IV_HISTORY_PATH"]).resolve()))

        state.run_scan()
        state.iv_store.flush()
        state.bar_cache.flush()

    def test_iv_history_store_concurrent_access_is_thread_safe(self):
        store = IVHistoryStore(persist_path=None)
        errors = []

        def worker(sym_idx):
            try:
                sym = f"SYM{sym_idx % 5}"
                for day in range(100):
                    store.add_data_point(sym, IVDataPoint(trade_date=f"2026-01-{day+1:02d}", atm_iv=0.25))
                    _ = store.get_metrics(sym, current_iv=0.25)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent store access produced errors: {errors}")

    def test_app_state_get_boards_concurrent_init(self):
        state = AppState(offline_mode=True)
        self.assertIsNone(state.ranker)

        results = []
        errors = []

        def call_boards():
            try:
                b = state.get_boards(alpha=0.5)
                results.append(b)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_boards) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent get_boards produced errors: {errors}")
        self.assertEqual(len(results), 5)
        for b in results:
            self.assertIn("deep_itm", b)


if __name__ == "__main__":
    unittest.main()
