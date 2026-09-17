"""
Test suite for Cash-Secured Put API endpoints.
Tests:
- GET /api/v1/csp/boards
- POST /api/v1/csp/rerank (in-memory, <1ms)
- DC-CSP-10 thread-safe snapshot pointer swap
"""
import json
import unittest

from src.leaps_scanner.api.server import AppState, create_api_handler_class
from src.leaps_scanner.scoring.csp_ranker import CSPCandidate, EarningsStatus


class TestCSPApi(unittest.TestCase):
    def setUp(self):
        self.state = AppState(offline_mode=True)
        self.handler_cls = create_api_handler_class(self.state)

    def test_get_csp_boards_offline(self):
        """Test GET /api/v1/csp/boards returns 3 boards."""
        code, headers, body = self.handler_cls.dispatch("GET", "/api/v1/csp/boards?alpha=0.5&cash_pool=50000", b"")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("boards", data)
        self.assertIn("harvest", data["boards"])
        self.assertIn("wheel", data["boards"])
        self.assertIn("vol_rank", data["boards"])
        self.assertEqual(data["cash_pool"], 50000.0)

    def test_post_csp_rerank_in_memory(self):
        """Test POST /api/v1/csp/rerank performs instant in-memory recalculation."""
        # Initial query to populate candidates
        self.handler_cls.dispatch("GET", "/api/v1/csp/boards", b"")

        # Rerank with different alpha and filter settings
        req = {
            "alpha": 0.8,
            "cash_pool": 30000.0,
            "config": {
                "filter_aroc": True,
                "min_aroc": 0.10,
                "filter_buffer": True,
                "min_buffer": 0.05,
                "filter_pop": True,
                "min_pop": 0.65,
                "filter_earnings": True,
                "strict_earnings": False,
                "filter_liquidity": True
            }
        }
        code, headers, body = self.handler_cls.dispatch("POST", "/api/v1/csp/rerank", json.dumps(req).encode("utf-8"))
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["recalculation_mode"], "IN_MEMORY_ZERO_NETWORK")
        self.assertEqual(data["alpha"], 0.8)
        self.assertEqual(data["cash_pool"], 30000.0)
        self.assertIn("harvest", data["boards"])

    def test_dc_csp_10_immutable_snapshot_pointer_swap(self):
        """DC-CSP-10: Test immutable snapshot structure and atomic pointer swap in AppState."""
        from dataclasses import FrozenInstanceError
        from src.leaps_scanner.scoring.csp_ranker import CSPBoardSnapshot

        # 1. Verify snapshot is initially None
        self.assertIsNone(self.state.csp_snapshot)

        # 2. Query boards - must populate snapshot
        self.state.get_csp_boards(alpha=0.5, cash_pool=50000.0)
        snap1 = self.state.csp_snapshot
        self.assertIsInstance(snap1, CSPBoardSnapshot)
        self.assertEqual(snap1.alpha, 0.5)
        self.assertEqual(snap1.cash_pool, 50000.0)
        self.assertIn("harvest", snap1.boards)

        # 3. Verify snapshot immutability (frozen dataclass)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            snap1.alpha = 0.9  # type: ignore

        # 4. Atomic pointer swap on rerank / update
        self.state.get_csp_boards(alpha=0.7, cash_pool=60000.0)
        snap2 = self.state.csp_snapshot
        self.assertIsInstance(snap2, CSPBoardSnapshot)
        self.assertIsNot(snap1, snap2)  # New snapshot object created
        self.assertEqual(snap2.alpha, 0.7)
        self.assertEqual(snap2.cash_pool, 60000.0)
        # Verify original snap1 remained untouched
        self.assertEqual(snap1.alpha, 0.5)
        self.assertEqual(snap1.cash_pool, 50000.0)


if __name__ == "__main__":
    unittest.main()
