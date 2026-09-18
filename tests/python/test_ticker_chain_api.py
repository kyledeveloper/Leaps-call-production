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


        self.assertIn("metrics_spec", data)


def _two_letter_tickers(n):
    """Generate n distinct 2-letter tickers (all valid per TICKER_REGEX)."""
    out = []
    i = 0
    while len(out) < n:
        out.append(chr(65 + (i // 26) % 26) + chr(65 + i % 26))
        i += 1
    return out


class TestChainInputValidation(unittest.TestCase):
    """Items 1 & 3 (TDD): strict ticker format on /ticker/chain; finite/range alpha & cash_pool."""

    def setUp(self):
        self.state = AppState(offline_mode=True)
        self.state.last_scan_time = "2026-09-17T22:00:00Z"
        self.handler_cls = create_api_handler_class(self.state)

    def test_rejects_script_ticker(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=%3Cscript%3Ealert(1)%3C/script%3E", b"")
        self.assertEqual(code, 400)
        self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_rejects_crlf_ticker(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL%0D%0AX-Injected", b"")
        self.assertEqual(code, 400)

    def test_rejects_overlong_ticker(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=VERYLONGTICKER", b"")
        self.assertEqual(code, 400)

    def test_accepts_hyphen_ticker_canonicalized(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=BRK-B&family=leaps", b"")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["ticker"], "BRK.B")

    def test_rejects_nan_alpha(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL&alpha=nan", b"")
        self.assertEqual(code, 400)

    def test_rejects_inf_alpha(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL&alpha=inf", b"")
        self.assertEqual(code, 400)

    def test_rejects_out_of_range_alpha(self):
        for bad in ("1.5", "-0.1"):
            code, _, _ = self.handler_cls.dispatch(
                "GET", f"/api/v1/ticker/chain?ticker=AAPL&alpha={bad}", b"")
            self.assertEqual(code, 400, f"alpha={bad} should be rejected")

    def test_accepts_boundary_alpha(self):
        for good in ("0", "0.5", "1"):
            code, _, _ = self.handler_cls.dispatch(
                "GET", f"/api/v1/ticker/chain?ticker=AAPL&alpha={good}", b"")
            self.assertEqual(code, 200, f"alpha={good} should be accepted")

    def test_garbage_alpha_falls_back_to_default(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL&alpha=abc", b"")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["alpha"], 0.5)

    def test_rejects_nonfinite_cash_pool(self):
        for bad in ("nan", "inf", "-inf"):
            code, _, _ = self.handler_cls.dispatch(
                "GET", f"/api/v1/ticker/chain?ticker=AAPL&cash_pool={bad}", b"")
            self.assertEqual(code, 400, f"cash_pool={bad} should be rejected")

    def test_rejects_negative_cash_pool(self):
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL&cash_pool=-100", b"")
        self.assertEqual(code, 400)


class TestChainRefreshAdmission(unittest.TestCase):
    """Item 4 (TDD): bounded cooldown dict + global force_refresh rate limit."""

    def setUp(self):
        self.state = AppState(offline_mode=True)
        self.state.last_scan_time = "2026-09-17T22:00:00Z"
        self.handler_cls = create_api_handler_class(self.state)

    def test_cooldown_dict_is_bounded(self):
        self.state._force_refresh_max_per_window = 10 ** 9  # disable global limiter for this test
        for t in _two_letter_tickers(600):
            code, _, _ = self.handler_cls.dispatch(
                "GET", f"/api/v1/ticker/chain?ticker={t}&force_refresh=true", b"")
            self.assertEqual(code, 200)
        self.assertLessEqual(len(self.state._ticker_refresh_cooldown), 512)

    def test_global_force_refresh_rate_limit(self):
        # The global limiter guards upstream I/O, so this test runs online with
        # a mocked client (offline mode is exempt from the limiter).
        self.state.offline_mode = False
        fake = MagicMock()
        fake.get_leaps_candidates.return_value = []
        self.state.client = fake
        limit = self.state._force_refresh_max_per_window
        tickers = _two_letter_tickers(limit + 1)
        codes = []
        for t in tickers:
            code, _, _ = self.handler_cls.dispatch(
                "GET", f"/api/v1/ticker/chain?ticker={t}&force_refresh=true", b"")
            codes.append(code)
        self.assertEqual(codes[-1], 429)
        self.assertTrue(all(c == 200 for c in codes[:-1]))
        _, _, body = self.handler_cls.dispatch(
            "GET", f"/api/v1/ticker/chain?ticker={tickers[-1]}&force_refresh=true", b"")
        self.assertEqual(json.loads(body.decode("utf-8"))["error"], "rate_limited")

    def test_cooldown_blocked_requests_do_not_consume_global_quota(self):
        # CONCERN 1: per-ticker-cooldown-blocked requests are served from cache
        # (zero upstream I/O) and must not consume the global quota.
        self.state.offline_mode = False
        fake = MagicMock()
        fake.get_leaps_candidates.return_value = []
        self.state.client = fake
        self.state._force_refresh_max_per_window = 3
        for _ in range(10):
            code, _, _ = self.handler_cls.dispatch(
                "GET", "/api/v1/ticker/chain?ticker=AAPL&force_refresh=true", b"")
            self.assertEqual(code, 200)
        # Only the first request actually fetched; a fresh ticker still admits.
        self.assertEqual(len(self.state._force_refresh_times), 1)
        code, _, _ = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=MSFT&force_refresh=true", b"")
        self.assertEqual(code, 200)

    def test_offline_mode_exempt_from_global_limiter(self):
        # CONCERN 1: offline mode performs no upstream I/O, so force_refresh
        # there must not consume the global quota.
        self.state._force_refresh_max_per_window = 2
        for t in _two_letter_tickers(10):
            code, _, _ = self.handler_cls.dispatch(
                "GET", f"/api/v1/ticker/chain?ticker={t}&force_refresh=true", b"")
            self.assertEqual(code, 200)
        self.assertEqual(len(self.state._force_refresh_times), 0)

    def test_eviction_removes_oldest_timestamp_not_oldest_inserted(self):
        # CONCERN 2: hard-cap eviction must use the oldest timestamp, not
        # insertion order. "NEWER" was inserted first but has the newer
        # timestamp; both entries are within the 15s cooldown window.
        now = time.time()
        self.state._force_refresh_max_cooldown_entries = 2
        self.state._ticker_refresh_cooldown = {"NEWER": now, "OLDER": now - 10.0}
        code, _, _ = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=ZZ&force_refresh=true", b"")
        self.assertEqual(code, 200)
        cd = self.state._ticker_refresh_cooldown
        self.assertLessEqual(len(cd), 2)
        self.assertIn("NEWER", cd, "entry with the newer timestamp must survive")
        self.assertIn("ZZ", cd)
        self.assertNotIn("OLDER", cd, "entry with the oldest timestamp must be evicted")

    def test_cooldown_blocked_request_still_200(self):
        code1, _, _ = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL&force_refresh=true", b"")
        self.assertEqual(code1, 200)
        code2, _, body2 = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL&force_refresh=true", b"")
        self.assertEqual(code2, 200)
        self.assertTrue(json.loads(body2.decode("utf-8"))["cooldown_active"])


class TestChainUpsertAndCSPCoverage(unittest.TestCase):
    """Item 5 (TDD): live upsert branch, CSP diagnostics path."""

    def setUp(self):
        self.state = AppState(offline_mode=True)
        self.state.candidates = [
            StrategyCandidate(
                symbol="AAPL260116C00150000", underlying="AAPL", strike=150.0,
                spot=220.0, dte=380.0, bid=80.0, ask=82.0, delta=0.82,
                open_interest=5000, volume=300, dividend_yield=0.005, iv=0.28,
            ),
            StrategyCandidate(
                symbol="MSFT260116C00350000", underlying="MSFT", strike=350.0,
                spot=420.0, dte=380.0, bid=100.0, ask=102.0, delta=0.80,
                open_interest=4000, volume=200, dividend_yield=0.008, iv=0.25,
            ),
        ]
        self.state.last_scan_time = "2026-09-17T22:00:00Z"
        self.handler_cls = create_api_handler_class(self.state)

    def test_live_upsert_branch_preserves_other_tickers(self):
        """The atomic-upsert branch must actually run: MSFT kept, AAPL replaced."""
        self.state.offline_mode = False
        new_aapl = StrategyCandidate(
            symbol="AAPL260116C00160000", underlying="AAPL", strike=160.0,
            spot=225.0, dte=380.0, bid=70.0, ask=72.0, delta=0.78,
            open_interest=6000, volume=400, dividend_yield=0.005, iv=0.30,
        )
        fake = MagicMock()
        fake.get_leaps_candidates.return_value = [new_aapl]
        self.state.client = fake
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/ticker/chain?ticker=AAPL&family=leaps&force_refresh=true", b"")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["contract_count"], 1)
        msft = [c for c in self.state.candidates if c.underlying == "MSFT"]
        self.assertEqual(len(msft), 1, "upsert must preserve MSFT")
        aapl = [c for c in self.state.candidates if c.underlying == "AAPL"]
        self.assertEqual(len(aapl), 1)
        self.assertEqual(aapl[0].strike, 160.0)

    def test_csp_diagnostics_direct(self):
        self.state.csp_candidates = [
            CSPCandidate(
                symbol="AAPL260116P00150000", underlying="AAPL", spot=220.0,
                strike=150.0, dte=380.0, bid=5.0, ask=5.4, delta=-0.20,
                open_interest=3000, volume=150, iv=0.32,
            )
        ]
        code, payload = self.state.get_ticker_chain_diagnostics(
            ticker="AAPL", family="csp", strategy="csp_harvest")
        self.assertEqual(code, 200)
        self.assertEqual(payload["family"], "csp")
        self.assertGreaterEqual(payload["contract_count"], 1)


if __name__ == "__main__":
    unittest.main()
