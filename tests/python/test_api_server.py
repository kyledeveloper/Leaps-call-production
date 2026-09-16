import json
import unittest
from tests.python.conftest import block_network
from src.leaps_scanner.api.server import create_api_handler_class, AppState
from io import BytesIO


class MockHTTPRequest:
    def __init__(self, method: str, path: str, body: bytes = b""):
        self.method = method
        self.path = path
        self.body = body


class TestAPIServer(unittest.TestCase):
    def setUp(self):
        block_network()
        self.state = AppState(offline_mode=True)
        # Seed candidates
        self.state.client.get_leaps_candidates(["AAPL", "SPY"])

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


if __name__ == "__main__":
    unittest.main()


