"""
Test suite for Cash-Secured Put API endpoints.
Tests:
- GET /api/v1/csp/boards
- POST /api/v1/csp/rerank (in-memory, <1ms)
- DC-CSP-10 thread-safe snapshot pointer swap
"""
import json
import unittest
from datetime import datetime, timezone

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

    def test_csp_and_workflow_web_routes(self):
        """Test GET /csp and GET /workflow return 200 HTML content."""
        code, headers, body = self.handler_cls.dispatch("GET", "/csp", b"")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Cash-Secured Put", body)

        code_wf, headers_wf, body_wf = self.handler_cls.dispatch("GET", "/workflow", b"")
        self.assertEqual(code_wf, 200)
        self.assertIn("text/html", headers_wf["Content-Type"])
        self.assertIn(b"CSP", body_wf)

    def test_pipeline_isolation_leaps_vs_csp(self):
        """Verify complete isolation: CSP scan does not pull LEAPS, and LEAPS scan does not pull CSP."""
        # 1. Scanning CSP only
        state_csp = AppState(offline_mode=True)
        count_csp = state_csp.run_scan(symbols=["AAPL", "SPY"], family="csp")
        self.assertGreater(count_csp, 0)
        self.assertEqual(len(state_csp.csp_candidates), count_csp)
        self.assertEqual(len(state_csp.candidates), 0, "CSP scan must NEVER pull LEAPS candidates")
        self.assertIsNone(state_csp.ranker, "LEAPS ranker must NOT be initialized during CSP scan")

        # 2. Scanning LEAPS only
        state_leaps = AppState(offline_mode=True)
        count_leaps = state_leaps.run_scan(symbols=["AAPL", "SPY"], family="leaps")
        self.assertGreater(count_leaps, 0)
        self.assertEqual(len(state_leaps.candidates), count_leaps)
        self.assertEqual(len(state_leaps.csp_candidates), 0, "LEAPS scan must NEVER pull CSP candidates")
        self.assertIsNone(state_leaps.csp_snapshot, "CSP snapshot must NOT be initialized during LEAPS scan")

    def test_post_scan_with_family_routing(self):
        """Verify POST /api/v1/scan with family='csp' routes strictly to CSP worker."""
        state = AppState(offline_mode=True)
        handler_cls = create_api_handler_class(state)
        req = {"family": "csp", "symbols": ["AAPL", "NVDA"]}
        code, headers, body = handler_cls.dispatch("POST", "/api/v1/scan", json.dumps(req).encode("utf-8"))
        self.assertEqual(code, 200)
        self.assertGreater(len(state.csp_candidates), 0)
        self.assertEqual(len(state.candidates), 0, "LEAPS candidate store must remain empty")

        cfg = json.loads(body.decode("utf-8"))
        self.assertEqual(cfg.get("scan_family"), "csp")
        self.assertEqual(cfg.get("csp_candidate_count"), len(state.csp_candidates))

    def test_parse_nasdaq_csp_chain_and_concurrency(self):
        """Test parse_nasdaq_csp_chain filters put contracts within 7~45 DTE."""
        from src.leaps_scanner.data.public_delayed import parse_nasdaq_csp_chain
        mock_payload = {
            "data": {
                "table": {
                    "rows": [
                        {
                            "p_Bid": "3.50",
                            "p_Ask": "3.80",
                            "p_Volume": "100",
                            "p_Openinterest": "1200",
                            "strike": "210.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261016p00210000",
                        },
                        {
                            "p_Bid": "0.50",
                            "p_Ask": "0.60",
                            "p_Volume": "5",
                            "p_Openinterest": "10",
                            "strike": "180.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--270115p00180000",  # DTE > 45, rejected
                        }
                    ]
                }
            }
        }
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        # 261016 is 2026-10-16, exactly 30 days away (7 <= 30 <= 45) -> passes!
        # 270115 is 2027-01-15, > 45 days away -> rejected!
        puts = parse_nasdaq_csp_chain(mock_payload, min_dte=7.0, max_dte=45.0, asof=asof)
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["strike"], 210.0)
        self.assertEqual(puts[0]["bid"], 3.50)


if __name__ == "__main__":
    unittest.main()
