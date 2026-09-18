import json
import os
import tempfile
import unittest
from tests.python.conftest import block_network
from src.leaps_scanner.api.server import create_api_handler_class, AppState
from src.leaps_scanner.data.rebalancer import DynamicUniverseManager
from io import BytesIO


class MockHTTPRequest:
    def __init__(self, method: str, path: str, body: bytes = b""):
        self.method = method
        self.path = path
        self.body = body


class TestAPIServer(unittest.TestCase):
    def setUp(self):
        block_network()
        self.tmp_dir = tempfile.TemporaryDirectory()
        tmp_cache_path = os.path.join(self.tmp_dir.name, "universe_cache.json")
        self.test_mgr = DynamicUniverseManager(cache_path=tmp_cache_path, offline_mode=True)
        self.state = AppState(offline_mode=True, universe_manager=self.test_mgr)
        # Seed candidates
        self.state.client.get_leaps_candidates(["AAPL", "SPY"])

    def tearDown(self):
        if hasattr(self, "tmp_dir"):
            self.tmp_dir.cleanup()

    def test_app_state_scan_and_rerank(self):
        self.state.run_scan(symbols=["AAPL", "SPY"])
        self.assertIsNotNone(self.state.ranker)

        # Get boards with alpha=0.5
        boards = self.state.get_boards(alpha=0.5)
        self.assertIn("deep_itm", boards)
        self.assertIn("vol_discount", boards)
        self.assertIn("oversold", boards)
        self.assertNotIn("unusual_flow", boards)

        # Rerank with alpha=1.0
        boards_10 = self.state.get_boards(alpha=1.0)
        self.assertEqual(len(boards_10["deep_itm"]), len(boards["deep_itm"]))

    def test_api_handler_dispatch(self):
        from http.server import BaseHTTPRequestHandler

        handler_cls = create_api_handler_class(self.state)

        # Test GET /api/v1/config
        code, headers, body = handler_cls.dispatch("GET", "/api/v1/config", b"")
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertEqual(res["status"], "healthy")
        self.assertTrue(res["offline_mode"])

        # Test POST /api/v1/scan
        scan_payload = json.dumps({"symbols": ["AAPL", "SPY"]}).encode("utf-8")
        code, headers, body = handler_cls.dispatch("POST", "/api/v1/scan", scan_payload)
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertGreater(res["candidate_count"], 0)

        # Test GET /api/v1/boards
        code, headers, body = handler_cls.dispatch("GET", "/api/v1/boards?alpha=0.5", b"")
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertIn("boards", res)
        self.assertIn("deep_itm", res["boards"])

        # Test POST /api/v1/rerank
        rerank_payload = json.dumps({"alpha": 0.8}).encode("utf-8")
        code, headers, body = handler_cls.dispatch("POST", "/api/v1/rerank", rerank_payload)
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertEqual(res["alpha"], 0.8)

        # Test GET /api/v1/universe
        code, headers, body = handler_cls.dispatch("GET", "/api/v1/universe", b"")
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertEqual(res["status"], "healthy")
        self.assertIn("indices", res)
        self.assertEqual(res["indices"]["djia"], 30)
        self.assertGreaterEqual(res["master_count"], 140)

        # Test POST /api/v1/universe/sync
        code, headers, body = handler_cls.dispatch("POST", "/api/v1/universe/sync", b"")
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertIn("results", res)
        self.assertIn("djia", res["results"])

    def test_live_mode_requires_credentials(self):
        handler_cls = create_api_handler_class(self.state)
        code, headers, body = handler_cls.dispatch(
            "POST",
            "/api/v1/mode",
            json.dumps({"offline": False}).encode("utf-8"),
        )
        self.assertEqual(code, 400)
        res = json.loads(body.decode("utf-8"))
        self.assertEqual(res["error"], "missing_credentials")
        self.assertTrue(res["offline_mode"])

        code, headers, body = handler_cls.dispatch(
            "POST",
            "/api/v1/mode",
            json.dumps({"offline": True}).encode("utf-8"),
        )
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertTrue(res["offline_mode"])
        self.assertGreater(res["candidate_count"], 0)

        code, headers, body = handler_cls.dispatch("GET", "/api/v1/config", b"")
        self.assertEqual(code, 200)
        cfg = json.loads(body.decode("utf-8"))
        self.assertIn("has_credentials", cfg)
        self.assertIn("connection_status", cfg)
        self.assertEqual(cfg.get("source"), "sandbox")

    def test_universe_scan_tiers(self):
        etfs = self.state.resolve_scan_symbols("etfs")
        djia = self.state.resolve_scan_symbols("djia")
        sp100 = self.state.resolve_scan_symbols("sp100")
        ndx = self.state.resolve_scan_symbols("ndx")
        npx = self.state.resolve_scan_symbols("npx")
        core = self.state.resolve_scan_symbols("core")

        self.assertGreaterEqual(len(etfs), 12)
        self.assertEqual(len(djia), 30)
        self.assertGreaterEqual(len(sp100), 100)
        self.assertGreaterEqual(len(ndx), 100)
        self.assertEqual(ndx, npx)
        self.assertGreaterEqual(len(core), 140)
        self.assertTrue(set(etfs).issubset(set(core)))
        self.assertTrue(set(djia).issubset(set(core)))
        self.assertTrue(set(sp100).issubset(set(core)))
        self.assertTrue(set(ndx).issubset(set(core)))

        handler = create_api_handler_class(self.state)

        # Test SP100 scan
        code, _, body = handler.dispatch(
            "POST", "/api/v1/scan", json.dumps({"tier": "sp100"}).encode()
        )
        self.assertEqual(code, 200)
        res = json.loads(body.decode())
        self.assertEqual(res["scan_tier"], "sp100")
        self.assertGreater(res["candidate_count"], 0)

        # Test NDX scan
        code, _, body = handler.dispatch(
            "POST", "/api/v1/scan", json.dumps({"tier": "ndx"}).encode()
        )
        self.assertEqual(code, 200)
        res = json.loads(body.decode())
        self.assertEqual(res["scan_tier"], "ndx")

        # Test NPX alias scan (must normalize to canonical ndx!)
        code, _, body = handler.dispatch(
            "POST", "/api/v1/scan", json.dumps({"tier": "npx"}).encode()
        )
        self.assertEqual(code, 200)
        res = json.loads(body.decode())
        self.assertEqual(res["scan_tier"], "ndx")

        # Test config contains sp100 and ndx
        code, _, body = handler.dispatch("GET", "/api/v1/config", b"")
        self.assertEqual(code, 200)
        cfg = json.loads(body.decode())
        self.assertIn("sp100", cfg["universe"])
        self.assertIn("ndx", cfg["universe"])
        self.assertGreaterEqual(cfg["universe"]["sp100"], 100)
        self.assertGreaterEqual(cfg["universe"]["ndx"], 100)

        # Switching tiers while a scan is running replaces the in-flight job.
        self.state.scan_status = "running"
        self.state.scan_tier = "etfs"
        code, _, body = handler.dispatch(
            "POST", "/api/v1/scan", json.dumps({"tier": "sp100"}).encode()
        )
        self.assertEqual(code, 200)
        self.assertEqual(self.state.scan_tier, "sp100")
        self.state.scan_status = "idle"

        # Same-tier request while running still 409 and does not mutate the tier.
        self.state.scan_status = "running"
        self.state.scan_tier = "ndx"
        code, _, body = handler.dispatch(
            "POST", "/api/v1/scan", json.dumps({"tier": "ndx"}).encode()
        )
        self.assertEqual(code, 409)
        self.assertEqual(self.state.scan_tier, "ndx")
        self.state.scan_status = "idle"

    def test_delayed_scan_passes_full_symbol_list(self):
        class FakeDelayed:
            def __init__(self):
                self.calls = []

            def get_leaps_candidates(self, symbols, progress_cb=None, should_stop=None):
                self.calls.append(list(symbols))
                for i, sym in enumerate(symbols, 1):
                    if progress_cb:
                        progress_cb(i, len(symbols), sym, [])
                return []

        fake = FakeDelayed()
        self.state.source = "delayed"
        self.state.client = fake
        self.state._scan_seq = 7
        self.state._scan_cancel = False
        self.state._scan_worker(["AAPL", "MSFT", "NVDA"], seq=7)
        self.assertEqual(fake.calls, [["AAPL", "MSFT", "NVDA"]])
        self.assertEqual(self.state.scan_status, "done")
        self.assertEqual(self.state.scan_progress["done"], 3)

    def test_delayed_scan_exception_sets_error_status(self):
        class BoomDelayed:
            def get_leaps_candidates(self, symbols, progress_cb=None, should_stop=None):
                raise RuntimeError("nasdaq down")

        self.state.source = "delayed"
        self.state.client = BoomDelayed()
        self.state._scan_seq = 3
        self.state._scan_cancel = False
        self.state._scan_worker(["AAPL"], seq=3)
        self.assertEqual(self.state.scan_status, "error")
        self.assertIn("scan_failed", self.state.last_error or "")

    def test_watchlist_api_endpoints_and_empty_guard(self):
        handler_cls = create_api_handler_class(self.state)

        # 1. GET /api/v1/watchlist returns default seed tickers
        code, _, body = handler_cls.dispatch("GET", "/api/v1/watchlist", b"")
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertIn("watchlist", res)
        self.assertIn("symbols", res)
        self.assertEqual(res["symbols"], res["watchlist"])
        self.assertGreaterEqual(res["count"], 1)

        # 2. POST /api/v1/watchlist add ticker using {"symbol": "$PLTR"} (Frontend format)
        add_payload = json.dumps({"symbol": "$PLTR"}).encode("utf-8")
        code, _, body = handler_cls.dispatch("POST", "/api/v1/watchlist", add_payload)
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertIn("PLTR", res["symbols"])
        self.assertIn("PLTR", res["watchlist"])

        # 3. DELETE /api/v1/watchlist ticker using query param ?symbol=PLTR (Frontend format)
        code, _, body = handler_cls.dispatch("DELETE", "/api/v1/watchlist?symbol=PLTR", b"")
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertNotIn("PLTR", res["symbols"])
        self.assertNotIn("PLTR", res["watchlist"])

        # 4. Also verify legacy {"ticker": "..."} payload format works
        add_payload_legacy = json.dumps({"action": "add", "ticker": "GME"}).encode("utf-8")
        code, _, body = handler_cls.dispatch("POST", "/api/v1/watchlist", add_payload_legacy)
        self.assertEqual(code, 200)
        del_payload_legacy = json.dumps({"ticker": "GME"}).encode("utf-8")
        code, _, body = handler_cls.dispatch("DELETE", "/api/v1/watchlist", del_payload_legacy)
        self.assertEqual(code, 200)

        # 5. Clause 5: Empty Watchlist Guard in resolve_scan_symbols and request_scan
        self.state.universe_manager.set_watchlist([])
        resolved = self.state.resolve_scan_symbols(tier="watchlist")
        self.assertEqual(resolved, [])  # Must NOT fallback to DEFAULT_SCAN_SYMBOLS

        # Empty scan request must return HTTP 400, but still land on the watchlist tier
        # so the UI does not snap back to the previous universe.
        self.state.scan_tier = "etfs"
        self.state.scan_status = "running"
        code, payload = self.state.request_scan(tier="watchlist")
        self.assertEqual(code, 400)
        self.assertEqual(payload.get("error"), "empty_watchlist")
        self.assertEqual(self.state.scan_tier, "watchlist")
        self.assertEqual(payload.get("scan_tier"), "watchlist")
        self.assertNotEqual(self.state.scan_status, "running")

    def test_watchlist_100_cap_enforced(self):
        """Item 5 (TDD): set truncates at 100; add beyond cap is rejected with 400."""
        handler_cls = create_api_handler_class(self.state)
        tickers = [chr(65 + (i // 26) % 26) + chr(65 + i % 26) for i in range(105)]
        set_payload = json.dumps({"action": "set", "tickers": tickers}).encode("utf-8")
        code, _, body = handler_cls.dispatch("POST", "/api/v1/watchlist", set_payload)
        self.assertEqual(code, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertEqual(res["count"], 100)

        add_payload = json.dumps({"action": "add", "ticker": "ZZZZZ"}).encode("utf-8")
        code, _, body = handler_cls.dispatch("POST", "/api/v1/watchlist", add_payload)
        self.assertEqual(code, 400)
        res = json.loads(body.decode("utf-8"))
        self.assertIn("capacity", res.get("message", ""))



if __name__ == "__main__":
    unittest.main()


