import threading
import time
import unittest

from tests.python.conftest import block_network
from src.leaps_scanner.api.server import AppState
from src.leaps_scanner.data.public_delayed import PublicDelayedClient, RateLimiter
from src.leaps_scanner.data.store.iv_history import IVHistoryStore
from src.leaps_scanner.data.store.daily_bars import DailyBarCache
from src.leaps_scanner.scoring.ranker import StrategyCandidate


class TestCancellationAndResilience(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_cancel_scan_increments_seq_and_aborts_delayed_worker(self):
        state = AppState(offline_mode=True)
        worker_started = threading.Event()
        can_finish = threading.Event()

        class SlowClient:
            def __init__(self):
                self.calls = 0

            def get_leaps_candidates(self, symbols, progress_cb=None, should_stop=None):
                self.calls += 1
                worker_started.set()
                can_finish.wait(timeout=3.0)
                if should_stop and should_stop():
                    return []
                dummy = StrategyCandidate(
                    symbol="DUMMY270115C00100000",
                    underlying="DUMMY",
                    strike=100.0,
                    spot=105.0,
                    dte=400.0,
                    bid=10.0,
                    ask=11.0,
                    delta=0.80,
                    open_interest=500,
                    volume=50,
                )
                if progress_cb:
                    progress_cb(1, 1, "DUMMY", [dummy])
                return [dummy]

        slow_client = SlowClient()
        state.source = "delayed"
        state.client = slow_client

        status_code, _ = state.request_scan(symbols=["DUMMY"])
        self.assertEqual(status_code, 202)
        initial_seq = state._scan_seq

        self.assertTrue(worker_started.wait(timeout=2.0))

        # Cancel the in-flight scan
        state.cancel_scan()
        self.assertGreater(state._scan_seq, initial_seq, "cancel_scan must increment _scan_seq")
        self.assertEqual(state.scan_status, "idle")

        can_finish.set()
        time.sleep(0.1)

        with state._lock:
            self.assertEqual(state.scan_status, "idle", "Cancelled worker must not set scan_status to 'done'")
            dummy_cands = [c for c in state.candidates if c.symbol == "DUMMY270115C00100000"]
            self.assertEqual(len(dummy_cands), 0, "Cancelled worker must not pollute state candidates")

    def test_set_mode_increments_scan_seq_and_invalidates_in_flight_scan(self):
        state = AppState(offline_mode=True)
        worker_started = threading.Event()
        can_finish = threading.Event()

        class SlowClient:
            def __init__(self):
                self.calls = 0

            def get_leaps_candidates(self, symbols, progress_cb=None, should_stop=None):
                self.calls += 1
                worker_started.set()
                can_finish.wait(timeout=3.0)
                if should_stop and should_stop():
                    return []
                dummy = StrategyCandidate(
                    symbol="DUMMY270115C00100000",
                    underlying="DUMMY",
                    strike=100.0,
                    spot=105.0,
                    dte=400.0,
                    bid=10.0,
                    ask=11.0,
                    delta=0.80,
                    open_interest=500,
                    volume=50,
                )
                if progress_cb:
                    progress_cb(1, 1, "DUMMY", [dummy])
                return [dummy]

        slow_client = SlowClient()
        state.source = "delayed"
        state.client = slow_client

        status_code, _ = state.request_scan(symbols=["DUMMY"])
        self.assertEqual(status_code, 202)
        initial_seq = state._scan_seq

        self.assertTrue(worker_started.wait(timeout=2.0))

        code, payload = state.set_mode(offline=True, source="sandbox")
        self.assertEqual(code, 200)

        self.assertGreater(state._scan_seq, initial_seq, "set_mode must increment _scan_seq to invalidate in-flight workers")

        can_finish.set()
        time.sleep(0.1)

        with state._lock:
            dummy_cands = [c for c in state.candidates if c.symbol == "DUMMY270115C00100000"]
            self.assertEqual(len(dummy_cands), 0, "Cancelled worker must not pollute state candidates")

    def test_rate_limiter_exits_early_when_stopped(self):
        lim = RateLimiter(min_interval_s=2.0)
        lim.wait()

        t_start = time.monotonic()
        lim.wait(should_stop=lambda: True)
        elapsed = time.monotonic() - t_start
        self.assertLess(elapsed, 0.2, f"RateLimiter.wait should return immediately when stopped, took {elapsed}s")

    def test_delayed_client_checkpoints_stop_early_without_mutating_stores(self):
        calls = []

        def fake_fetch(url, headers):
            calls.append(url)
            time.sleep(0.05)
            return 200, b'{"chart":{"result":[{"meta":{"regularMarketPrice":100},"timestamp":[],"indicators":{"quote":[{}]}}]}}'

        iv_store = IVHistoryStore()
        bar_cache = DailyBarCache(session_date_fn=lambda: "2026-09-16")
        client = PublicDelayedClient(
            fetch_fn=fake_fetch,
            iv_store=iv_store,
            bar_cache=bar_cache,
            max_workers=2,
            min_interval_s=0.0,
        )

        stop_flag = True
        t0 = time.monotonic()
        cands = client.get_leaps_candidates(["AAPL", "MSFT", "NVDA"], should_stop=lambda: stop_flag)
        elapsed = time.monotonic() - t0

        self.assertEqual(len(cands), 0)
        self.assertLess(elapsed, 0.3)
        self.assertEqual(len(iv_store._store), 0)


if __name__ == "__main__":
    unittest.main()
