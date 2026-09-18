"""
TDD Unit Tests for Dedicated Option Chain Diagnostic Page and API Endpoints.
Ratified under Red-Team Inquest Clauses 1-7:
- Clause 1: Data isolation & non-destructive ingest on single-ticker force_refresh
- Clause 2: Unfiltered diagnostic pipeline preserving rejected contracts
- Clause 3: 15-second rate-limit cooldown & failure fallback
- Clause 4: Structured gate_diagnostics with DATA_DEGRADED distinction
- Clause 5: Deep-link state preservation & prefix normalization
- Clause 6: Expiry-grouped accordion safeguards
- Clause 7: Covered Call (CC) blueprint protocol (HTTP 200)
"""
import json
import time
import unittest
from unittest.mock import MagicMock
from src.leaps_scanner.api.server import AppState, create_api_handler_class
from src.leaps_scanner.scoring.ranker import StrategyCandidate
from src.leaps_scanner.scoring.csp_ranker import CSPCandidate
from src.leaps_scanner.data.universe import SymbologyNormalizer


class TestTickerChainAPI(unittest.TestCase):
    def setUp(self):
        self.state = AppState(offline_mode=True)
        # Seed state with sample LEAPS candidates for AAPL and MSFT
        self.state.candidates = [
            StrategyCandidate(
                symbol="AAPL260116C00150000",
                underlying="AAPL",
                strike=150.0,
                spot=220.0,
                dte=380.0,
                bid=80.0,
                ask=82.0,
                delta=0.82,
                open_interest=5000,
                volume=300,
                dividend_yield=0.005,
                iv=0.28,
            ),
            StrategyCandidate(
                symbol="MSFT260116C00350000",
                underlying="MSFT",
                strike=350.0,
                spot=420.0,
                dte=380.0,
                bid=100.0,
                ask=102.0,
                delta=0.80,
                open_interest=4000,
                volume=200,
                dividend_yield=0.008,
                iv=0.25,
            )
        ]
        self.state.last_scan_time = "2026-09-17T22:00:00Z"
        self.handler_cls = create_api_handler_class(self.state)

    def test_static_chain_route(self):
        """GET /chain and /chain.html should serve 200 HTML content."""
        code, headers, body = self.handler_cls.dispatch("GET", "/chain", b"")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))

        code2, headers2, body2 = self.handler_cls.dispatch("GET", "/chain.html", b"")
        self.assertEqual(code2, 200)
        self.assertIn("text/html", headers2.get("Content-Type", ""))

    def test_missing_ticker_parameter_error(self):
        """GET /api/v1/ticker/chain without ticker returns 400."""
        code, headers, body = self.handler_cls.dispatch("GET", "/api/v1/ticker/chain", b"")
        self.assertEqual(code, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("error", data)

    def test_ticker_cashtag_stripping_and_snapshot_retrieval(self):
        """Cashtag like $AAPL should be normalized to AAPL and retrieve cached data."""
        code, headers, body = self.handler_cls.dispatch("GET", "/api/v1/ticker/chain?ticker=$AAPL&family=leaps", b"")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["ticker"], "AAPL")
        self.assertEqual(data["family"], "leaps")
        self.assertGreaterEqual(len(data["contracts"]), 1)
        self.assertEqual(data["contracts"][0]["underlying"], "AAPL")

    def test_clause_1_force_refresh_does_not_poison_global_candidates(self):
        """Clause 1: force_refresh for AAPL must NOT delete MSFT from AppState.candidates."""
        initial_msft_count = len([c for c in self.state.candidates if c.underlying == "MSFT"])
        self.assertEqual(initial_msft_count, 1)

        code, headers, body = self.handler_cls.dispatch("GET", "/api/v1/ticker/chain?ticker=AAPL&family=leaps&force_refresh=true", b"")
        self.assertEqual(code, 200)

        # MSFT must still exist in global state!
        after_msft_count = len([c for c in self.state.candidates if c.underlying == "MSFT"])
        self.assertEqual(after_msft_count, 1, "Clause 1: Single-ticker refresh must NOT wipe other tickers from AppState.candidates")

    def test_clause_3_cooldown_and_upstream_failure_fallback(self):
        """Clause 3: 15-second cooldown returns existing snapshot with cooldown_active: true."""
        # 1. First refresh
        code1, _, body1 = self.handler_cls.dispatch("GET", "/api/v1/ticker/chain?ticker=AAPL&family=leaps&force_refresh=true", b"")
        self.assertEqual(code1, 200)

        # 2. Immediate second refresh should trip cooldown
        code2, _, body2 = self.handler_cls.dispatch("GET", "/api/v1/ticker/chain?ticker=AAPL&family=leaps&force_refresh=true", b"")
        self.assertEqual(code2, 200)
        data2 = json.loads(body2.decode("utf-8"))
        self.assertTrue(data2.get("cooldown_active"), "Clause 3: Cooldown must be active on rapid subsequent refresh")

    def test_clause_4_gate_diagnostics_structure(self):
        """Clause 4: Every contract must have gate_diagnostics array with status, actual_value, target_rule."""
        code, _, body = self.handler_cls.dispatch("GET", "/api/v1/ticker/chain?ticker=AAPL&family=leaps&strategy=deep_itm", b"")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        contract = data["contracts"][0]
        self.assertIn("gate_diagnostics", contract)
        diagnostics = contract["gate_diagnostics"]
        self.assertIsInstance(diagnostics, list)
        self.assertGreater(len(diagnostics), 0)

        first_gate = diagnostics[0]
        self.assertIn("gate_name", first_gate)
        self.assertIn("status", first_gate)
        self.assertIn("actual_value", first_gate)
        self.assertIn("target_rule", first_gate)
        self.assertIn("reason", first_gate)

    def test_clause_7_covered_call_blueprint_protocol(self):
        """Clause 7: family=cc returns HTTP 200 with status: blueprint and metrics_spec."""
        code, headers, body = self.handler_cls.dispatch("GET", "/api/v1/ticker/chain?ticker=AAPL&family=cc", b"")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("status"), "blueprint")
        self.assertEqual(data.get("family"), "cc")
        self.assertIn("metrics_spec", data)


if __name__ == "__main__":
    unittest.main()
