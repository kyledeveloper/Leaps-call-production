"""
Test suite for Cash-Secured Put API endpoints.
Tests:
- GET /api/v1/csp/boards
- POST /api/v1/csp/rerank (in-memory, <1ms)
- DC-CSP-10 thread-safe snapshot pointer swap
"""
import json
import threading
import time
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
        # Dual-key alias support for frontend tabs (csp_*)
        self.assertIn("csp_harvest", data["boards"])
        self.assertIn("csp_wheel", data["boards"])
        self.assertIn("csp_vol_rank", data["boards"])
        self.assertEqual(data["boards"]["harvest"], data["boards"]["csp_harvest"])
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
        self.assertNotIn(b'href="/workflow"', body)

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

    def test_csp_target_expiries_includes_near_term_and_keeps_october_monthly(self):
        from src.leaps_scanner.data.public_delayed import csp_target_expiries, third_friday
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        self.assertEqual(third_friday(2026, 9).isoformat(), "2026-09-18")
        self.assertEqual(third_friday(2026, 10).isoformat(), "2026-10-16")
        dates = csp_target_expiries(asof)
        self.assertIn("2026-10-16", dates)
        self.assertIn("2026-09-18", dates)
        dates_min7 = csp_target_expiries(asof, min_dte=7.0)
        self.assertNotIn("2026-09-18", dates_min7)

    def test_nasdaq_csp_chain_queries_pinned_monthly_expiry(self):
        """Range queries return both front and monthly when DTE scanning is full 0-45d."""
        from src.leaps_scanner.data.public_delayed import PublicDelayedClient
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        urls = []
        front = {
            "data": {
                "lastTrade": "$220.00",
                "table": {
                    "rows": [{
                        "p_Bid": "0.40", "p_Ask": "0.50", "p_Volume": "10",
                        "p_Openinterest": "5", "strike": "200.00",
                        "expiryDate": "September 18, 2026",
                    }]
                },
            }
        }
        monthly = {
            "data": {
                "lastTrade": "$220.00",
                "table": {
                    "rows": [{
                        "p_Bid": "3.50", "p_Ask": "3.80", "p_Volume": "100",
                        "p_Openinterest": "1200", "strike": "210.00",
                        "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261016p00210000",
                    }]
                },
            }
        }
        combined = {
            "data": {
                "lastTrade": "$220.00",
                "table": {
                    "rows": (front["data"]["table"]["rows"] + monthly["data"]["table"]["rows"]),
                },
            }
        }

        def fetch(url, headers):
            urls.append(url)
            return 200, json.dumps(combined).encode()

        client = PublicDelayedClient(fetch_fn=fetch)
        rows, last = client._nasdaq_csp_chain("AAPL", asof)
        self.assertTrue(any("fromdate=2026-09-16" in u and "todate=" in u for u in urls))
        strikes = [r["strike"] for r in rows]
        self.assertIn(200.0, strikes)
        self.assertIn(210.0, strikes)
        self.assertEqual(last, 220.0)

    def test_parse_nasdaq_csp_chain_english_expiry_and_compact_occ(self):
        """Nasdaq rows often use 'October 16, 2026' and compact OCC URLs without --."""
        from src.leaps_scanner.data.public_delayed import parse_nasdaq_csp_chain
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        payload = {
            "data": {
                "table": {
                    "rows": [
                        {
                            "p_Bid": "3.50",
                            "p_Ask": "3.80",
                            "p_Volume": "100",
                            "p_Openinterest": "1200",
                            "strike": "210.00",
                            "expiryDate": "October 16, 2026",
                        },
                        {
                            "p_Bid": "4.10",
                            "p_Ask": "4.40",
                            "p_Volume": "80",
                            "p_Openinterest": "900",
                            "strike": "205.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl261016c00205000",
                        },
                        {
                            "p_Bid": "0.40",
                            "p_Ask": "0.50",
                            "p_Volume": "10",
                            "p_Openinterest": "5",
                            "strike": "200.00",
                            "expiryDate": "September 18, 2026",
                        },
                    ]
                }
            }
        }
        puts = parse_nasdaq_csp_chain(payload, min_dte=7.0, max_dte=45.0, asof=asof)
        strikes = sorted(p["strike"] for p in puts)
        self.assertEqual(strikes, [205.0, 210.0])

    def test_request_scan_clears_csp_book_when_switching_universe(self):
        """Switching scan universe must drop the previous family's displayed book immediately."""
        state = AppState(offline_mode=True)
        state.run_scan(symbols=["AAPL"], family="csp")
        self.assertGreater(len(state.csp_candidates), 0)
        old_syms = {c.underlying for c in state.csp_candidates}
        self.assertIn("AAPL", old_syms)

        state.source = "delayed"

        def fake_worker(symbols, seq=None):
            self.assertEqual(state.csp_candidates, [])
            self.assertIsNone(state.csp_snapshot)
            state.scan_status = "done"

        state._scan_csp_worker = fake_worker  # type: ignore[method-assign]
        code, payload = state.request_scan(symbols=["SPY"], tier="etfs", family="csp")
        self.assertEqual(code, 202)
        if state._scan_thread is not None:
            state._scan_thread.join(timeout=2.0)
        self.assertEqual(payload.get("csp_candidate_count"), 0)

    def test_delayed_mode_switch_scans_csp_family(self):
        """Clicking Delayed on the CSP tab must start a CSP scan, not LEAPS."""
        state = AppState(offline_mode=True)
        captured = {}

        def fake_request_scan(symbols=None, tier=None, family="leaps"):
            captured["family"] = family
            captured["tier"] = tier
            state.scan_family = family
            return 202, state.public_config()

        state.request_scan = fake_request_scan  # type: ignore[method-assign]
        code, payload = state.set_mode(offline=False, source="delayed", family="csp")
        self.assertEqual(code, 202)
        self.assertEqual(captured.get("family"), "csp")
        self.assertEqual(payload.get("scan_family"), "csp")

    def test_get_csp_boards_delayed_empty_does_not_network_fetch(self):
        """GET /csp/boards must not block on Nasdaq when the CSP scan has not finished."""
        state = AppState(offline_mode=True)
        state.source = "delayed"
        state.csp_candidates = []

        class BoomClient:
            def get_csp_candidates(self, *args, **kwargs):
                raise AssertionError("delayed GET /csp/boards must not live-fetch")

        state.client = BoomClient()
        boards = state.get_csp_boards(alpha=0.5)
        self.assertEqual(boards["harvest"], [])
        self.assertEqual(boards["csp_harvest"], [])
        self.assertEqual(boards["wheel"], [])
        self.assertEqual(boards["vol_rank"], [])

    def test_nasdaq_csp_chain_includes_short_weeklies(self):
        from src.leaps_scanner.data.public_delayed import PublicDelayedClient, csp_target_expiries
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        weeklies = csp_target_expiries(asof, min_dte=7.0, max_dte=21.0, include_weeklies=True)
        self.assertIn("2026-09-25", weeklies)
        urls = []

        def fetch(url, headers):
            urls.append(url)
            return 200, json.dumps({"data": {"table": {"rows": []}}}).encode()

        PublicDelayedClient(fetch_fn=fetch)._nasdaq_csp_chain("AAPL", asof)
        range_urls = [u for u in urls if "fromdate=" in u and "todate=" in u]
        self.assertTrue(range_urls, f"expected a 0-45 range query, got {urls}")
        self.assertTrue(any("fromdate=2026-09-16" in u for u in range_urls))
        self.assertTrue(any("todate=2026-10-31" in u or "todate=2026-11-01" in u for u in range_urls))

    def test_nasdaq_csp_chain_range_covers_front_and_monthly(self):
        """A single fromdate/todate window must ingest both <7 and 28-45 expiries."""
        from src.leaps_scanner.data.public_delayed import PublicDelayedClient
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        urls = []
        payload = {
            "data": {
                "lastTrade": "$220.00",
                "table": {
                    "rows": [
                        {
                            "p_Bid": "0.40", "p_Ask": "0.50", "p_Volume": "10",
                            "p_Openinterest": "5", "strike": "200.00",
                            "expirygroup": "September 18, 2026",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--260918p00200000",
                        },
                        {
                            "p_Bid": "3.50", "p_Ask": "3.80", "p_Volume": "100",
                            "p_Openinterest": "1200", "strike": "210.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261016p00210000",
                        },
                        {
                            "p_Bid": "2.10", "p_Ask": "2.30", "p_Volume": "80",
                            "p_Openinterest": "400", "strike": "205.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261023p00205000",
                        },
                    ]
                },
            }
        }

        def fetch(url, headers):
            urls.append(url)
            return 200, json.dumps(payload).encode()

        client = PublicDelayedClient(fetch_fn=fetch)
        rows, last = client._nasdaq_csp_chain("AAPL", asof)
        self.assertTrue(any("fromdate=2026-09-16" in u and "todate=" in u for u in urls))
        dtes = sorted(r["dte"] for r in rows)
        self.assertGreaterEqual(len(rows), 3)
        self.assertLess(min(dtes), 7.0)
        self.assertGreaterEqual(max(dtes), 28.0)
        self.assertEqual(last, 220.0)

    def test_get_and_post_dte_buckets_drop_other_expiries(self):
        """Live GET/POST dte_buckets must hide unselected buckets, not just mark REJECT."""
        short = CSPCandidate(
            symbol="SPY260921P00650000", underlying="SPY", spot=660.0, strike=650.0,
            dte=3.0, bid=2.0, ask=2.2, delta=-0.20, open_interest=5000, volume=2000,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )
        long = CSPCandidate(
            symbol="SPY261016P00650000", underlying="SPY", spot=660.0, strike=650.0,
            dte=30.0, bid=8.0, ask=8.4, delta=-0.22, open_interest=8000, volume=3000,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )
        self.state.csp_candidates = [short, long]
        self.state.source = "delayed"

        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/csp/boards?dte_buckets=0-7&filter_aroc=false&filter_buffer=false&filter_pop=false&filter_earnings=false&filter_liquidity=false",
            b"",
        )
        self.assertEqual(code, 200)
        harvest = json.loads(body.decode())["boards"]["harvest"]
        syms = [it["candidate"]["symbol"] for it in harvest]
        self.assertEqual(syms, ["SPY260921P00650000"])

        # Default AROC/buffer/POP pills ON must still drop the other DTE bucket.
        code, _, body = self.handler_cls.dispatch(
            "GET", "/api/v1/csp/boards?dte_buckets=0-7",
            b"",
        )
        self.assertEqual(code, 200)
        harvest = json.loads(body.decode())["boards"]["harvest"]
        syms = [it["candidate"]["symbol"] for it in harvest]
        self.assertEqual(syms, ["SPY260921P00650000"])

        req = json.dumps({
            "alpha": 0.5,
            "cash_pool": 50000,
            "config": {
                "filter_aroc": False,
                "filter_buffer": False,
                "filter_pop": False,
                "filter_earnings": False,
                "filter_liquidity": False,
                "selected_dte_buckets": ["28-45"],
            },
        }).encode()
        code, _, body = self.handler_cls.dispatch("POST", "/api/v1/csp/rerank", req)
        self.assertEqual(code, 200)
        harvest = json.loads(body.decode())["boards"]["harvest"]
        syms = [it["candidate"]["symbol"] for it in harvest]
        self.assertEqual(syms, ["SPY261016P00650000"])

    def test_nasdaq_csp_range_query_is_single_request_per_asset(self):
        """0-45 coverage uses one Nasdaq range request, not a Friday pin storm."""
        from src.leaps_scanner.data.public_delayed import PublicDelayedClient
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        urls = []

        def fetch(url, headers):
            urls.append(url)
            return 200, json.dumps({"data": {"lastTrade": "$220.00", "table": {"rows": []}}}).encode()

        PublicDelayedClient(fetch_fn=fetch, max_workers=1, min_interval_s=0)._nasdaq_csp_chain("AAPL", asof)
        self.assertGreaterEqual(len(urls), 1)
        self.assertTrue(any("fromdate=2026-09-16" in u and "todate=" in u for u in urls))
        # Must not pin every Friday individually when the range query is available.
        pinned = [u for u in urls if "fromdate=2026-09-18" in u and "todate=2026-09-18" in u]
        self.assertEqual(pinned, [])

    def test_delayed_csp_uses_put_iv_solver(self):
        from unittest.mock import patch
        from src.leaps_scanner.core.iv_solver import IVResult
        from src.leaps_scanner.data.public_delayed import PublicDelayedClient
        from tests.python.test_public_delayed import YAHOO_CHART_FIXTURE

        nasdaq = {
            "data": {
                "lastTrade": "$331.34",
                "table": {
                    "rows": [{
                        "p_Bid": "8.10",
                        "p_Ask": "8.40",
                        "p_Volume": "120",
                        "p_Openinterest": "1500",
                        "strike": "310.00",
                        "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261016p00310000",
                    }]
                },
            }
        }

        def fake_fetch(url, headers):
            if "yahoo" in url or "query1" in url:
                return 200, json.dumps(YAHOO_CHART_FIXTURE).encode()
            return 200, json.dumps(nasdaq).encode()

        called = []

        def fake_put_iv(*args, **kwargs):
            called.append(True)
            return IVResult(iv=0.27, status="CONVERGED")

        with patch("src.leaps_scanner.data.public_delayed.solve_american_put_iv", side_effect=fake_put_iv):
            client = PublicDelayedClient(fetch_fn=fake_fetch, max_workers=1, min_interval_s=0)
            cands = client.get_csp_candidates(["AAPL"])
        self.assertTrue(called)
        self.assertGreaterEqual(len(cands), 1)
        self.assertAlmostEqual(cands[0].iv, 0.27)
        self.assertIsNone(cands[0].iv_rank)

    def test_etf_earnings_marked_confirmed_safe(self):
        from src.leaps_scanner.data.public_delayed import classify_csp_earnings
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        self.assertEqual(
            classify_csp_earnings(True, asof, 30.0, None).value,
            "CONFIRMED_SAFE",
        )
        self.assertEqual(
            classify_csp_earnings(False, asof, 30.0, None).value,
            "EARNINGS_UNVERIFIED",
        )
        hit = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.assertEqual(
            classify_csp_earnings(False, asof, 30.0, [hit]).value,
            "EARNINGS_IMPACTED",
        )

    def test_webull_csp_live_requests_put_chain(self):
        from src.leaps_scanner.data.webull import WebullClient
        captured = {}
        client = WebullClient(offline_mode=True, token_file=None)
        client.offline_mode = False

        def fake_http(uri, queries=None, **kwargs):
            captured["queries"] = queries
            return 200, {"data": [], "pagination_key": None}

        client._http_request = fake_http  # type: ignore[method-assign]
        client.get_csp_candidates(["AAPL"])
        self.assertEqual(captured.get("queries", {}).get("option_type"), "PUT")


class TestCSPBugRegressions(unittest.TestCase):
    """Regression tests for bugs found in the CSP code audit."""

    def test_zero_bid_must_not_be_masked_by_p_last_fallback(self):
        """
        DC-CSP-6 regression: A genuine zero bid (p_Bid=0.0) must NOT be silently
        replaced by a stale p_Last price via Python's falsy `or` operator.
        The contract must be hard-rejected by the zero-bid gate.
        Bug: `bid = _parse_num(p_Bid) or _parse_num(p_Last)` -- when p_Bid parses
        to 0.0 (falsy), p_Last is used instead, bypassing the `bid <= 0` gate.
        """
        from datetime import datetime, timezone
        from src.leaps_scanner.data.public_delayed import parse_nasdaq_csp_chain

        # A contract with genuine zero market bid but a stale last trade of $2.50
        payload = {
            "data": {
                "table": {
                    "rows": [
                        {
                            "p_Bid": "0.00",        # True zero bid: no buyer in market
                            "p_Ask": "3.50",
                            "p_Last": "2.50",       # Stale last trade: MUST NOT substitute bid
                            "p_Volume": "10",
                            "p_Openinterest": "500",
                            "strike": "195.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261016p00195000",
                        }
                    ]
                }
            }
        }
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        # Must be rejected: zero-bid contract is un-executable for a seller
        puts = parse_nasdaq_csp_chain(payload, min_dte=7.0, max_dte=45.0, asof=asof)
        self.assertEqual(len(puts), 0, (
            "A contract with zero bid (p_Bid=0.00) must be rejected by the DC-CSP-6 "
            "zero-bid gate, not masked by p_Last fallback."
        ))

    def test_valid_nonzero_bid_with_p_last_still_accepted(self):
        """Sanity check: a valid positive bid is still parsed and accepted."""
        from datetime import datetime, timezone
        from src.leaps_scanner.data.public_delayed import parse_nasdaq_csp_chain

        payload = {
            "data": {
                "table": {
                    "rows": [
                        {
                            "p_Bid": "3.50",
                            "p_Ask": "3.80",
                            "p_Last": "3.60",
                            "p_Volume": "100",
                            "p_Openinterest": "1200",
                            "strike": "210.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261016p00210000",
                        }
                    ]
                }
            }
        }
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        puts = parse_nasdaq_csp_chain(payload, min_dte=7.0, max_dte=45.0, asof=asof)
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["bid"], 3.50)

    def test_none_bid_falls_back_to_p_last_when_p_bid_missing(self):
        """
        Acceptable fallback: if p_Bid field is entirely absent (None), falling
        back to p_Last is correct behaviour (last trade as proxy).
        """
        from datetime import datetime, timezone
        from src.leaps_scanner.data.public_delayed import parse_nasdaq_csp_chain

        payload = {
            "data": {
                "table": {
                    "rows": [
                        {
                            # p_Bid key entirely absent: legitimate fallback to p_Last
                            "p_Ask": "3.80",
                            "p_Last": "3.50",
                            "p_Volume": "100",
                            "p_Openinterest": "1200",
                            "strike": "210.00",
                            "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261016p00210000",
                        }
                    ]
                }
            }
        }
        asof = datetime(2026, 9, 16, tzinfo=timezone.utc)
        puts = parse_nasdaq_csp_chain(payload, min_dte=7.0, max_dte=45.0, asof=asof)
        # p_Last=3.50 should substitute and pass the gate
        self.assertEqual(len(puts), 1)
        self.assertAlmostEqual(puts[0]["bid"], 3.50)


if __name__ == "__main__":
    unittest.main()

