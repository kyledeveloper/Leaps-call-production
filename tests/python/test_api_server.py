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
        self.assertIn("unusual_flow", boards)

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


if __name__ == "__main__":
    unittest.main()
