import time
import unittest
from tests.python.conftest import block_network
from src.leaps_scanner.strategies.guards import GuardStatus


class TestWebullAdapter(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_token_bucket_rate_limiter(self):
        from src.leaps_scanner.data.webull import TokenBucketRateLimiter
        # Rate limiter with capacity 2 and refill rate 10 tokens/sec
        limiter = TokenBucketRateLimiter(rate=10.0, capacity=2.0)
        self.assertTrue(limiter.acquire(tokens=1.0))
        self.assertTrue(limiter.acquire(tokens=1.0))
        # Now bucket is empty
        self.assertFalse(limiter.acquire(tokens=1.0))

    def test_single_flight_token_manager(self):
        from src.leaps_scanner.data.webull import SingleFlightAuthManager

        call_count = 0

        def mock_refresh():
            nonlocal call_count
            call_count += 1
            time.sleep(0.01)
            return "new_valid_token_123"

        auth = SingleFlightAuthManager(refresh_fn=mock_refresh)
        # First call triggers refresh
        token1 = auth.get_token()
        self.assertEqual(token1, "new_valid_token_123")
        self.assertEqual(call_count, 1)

        # Subsequent call before expiry uses cached token
        token2 = auth.get_token()
        self.assertEqual(token2, "new_valid_token_123")
        self.assertEqual(call_count, 1)

    def test_webull_chain_parser_and_filter(self):
        from src.leaps_scanner.data.webull import parse_webull_chain_response

        raw_response = {
            "underlying": "AAPL",
            "spot": 220.0,
            "dividend_yield": 0.005,
            "options": [
                # Expiring in 30 days -> should be filtered out by LEAPS DTE >= 250
                {
                    "symbol": "AAPL241018C00220000",
                    "strike": 220.0,
                    "dte": 30.0,
                    "bid": 3.0,
                    "ask": 3.2,
                    "delta": 0.50,
                    "open_interest": 5000,
                    "volume": 1200,
                    "multiplier": 100
                },
                # Valid LEAPS call
                {
                    "symbol": "AAPL270115C00180000",
                    "strike": 180.0,
                    "dte": 480.0,
                    "bid": 55.0,
                    "ask": 57.0,
                    "delta": 0.76,
                    "open_interest": 2500,
                    "volume": 150,
                    "multiplier": 100
                },
                # Non-standard multiplier -> should be flagged
                {
                    "symbol": "AAPL270115C00180000_ADJ",
                    "strike": 180.0,
                    "dte": 480.0,
                    "bid": 55.0,
                    "ask": 57.0,
                    "delta": 0.76,
                    "open_interest": 2500,
                    "volume": 150,
                    "multiplier": 50
                }
            ]
        }

        candidates = parse_webull_chain_response(raw_response)
        # Only the valid 480 DTE contract with multiplier 100 should be retained
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].symbol, "AAPL270115C00180000")
        self.assertEqual(candidates[0].dte, 480.0)

    def test_mock_webull_adapter_offline_loading(self):
        from src.leaps_scanner.data.webull import WebullClient
        # Client initialized with offline_mode=True should safely use local fixtures
        client = WebullClient(offline_mode=True)
        candidates = client.get_leaps_candidates(["AAPL", "SPY"])
        self.assertGreater(len(candidates), 0)
        self.assertTrue(all(c.dte >= 250.0 for c in candidates))

    def test_signature_calculation_and_headers(self):
        from src.leaps_scanner.data.webull import WebullSigner
        headers, string_to_sign = WebullSigner.calc_signature(
            uri="/openapi/config",
            queries={"symbol": "AAPL"},
            body_dict=None,
            app_key="us.test_app_key_123",
            app_secret="test_app_secret_456",
            host="api.webull.com",
            nonce="fixed-test-nonce",
            timestamp="2026-09-15T22:00:00Z"
        )
        self.assertEqual(headers["x-app-key"], "us.test_app_key_123")
        self.assertEqual(headers["x-timestamp"], "2026-09-15T22:00:00Z")
        self.assertEqual(headers["x-signature-version"], "1.0")
        self.assertEqual(headers["x-signature-algorithm"], "HMAC-SHA256")
        self.assertEqual(headers["x-signature-nonce"], "fixed-test-nonce")
        self.assertTrue(bool(headers["x-signature"]))

    def test_log_sanitizer(self):
        from src.leaps_scanner.data.webull import LogSanitizer
        raw_headers = {
            "x-app-key": "us.45a4bd81dd25a0f9403b2c0be28869f9",
            "x-signature": "abcdef1234567890abcdef1234567890=",
            "x-access-token": "secret_live_token_999",
            "Content-Type": "application/json"
        }
        sanitized = LogSanitizer.mask_dict(raw_headers)
        self.assertEqual(sanitized["x-signature"], "***REDACTED***")
        self.assertEqual(sanitized["x-access-token"], "***REDACTED***")
        self.assertEqual(sanitized["Content-Type"], "application/json")

    def test_token_bucket_blocking(self):
        from src.leaps_scanner.data.webull import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter(rate=20.0, capacity=1.0)
        self.assertTrue(limiter.acquire(tokens=1.0))
        # Immediate second acquire without block fails
        self.assertFalse(limiter.acquire(tokens=1.0, block=False))
        # Acquire with blocking succeeds once refilled
        start = time.monotonic()
        self.assertTrue(limiter.acquire(tokens=1.0, block=True, timeout=0.2))
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.04)

    def test_single_flight_invalidate_and_refresh(self):
        from src.leaps_scanner.data.webull import SingleFlightAuthManager
        calls = 0

        def mock_fetch():
            nonlocal calls
            calls += 1
            return f"token_v{calls}"

        auth = SingleFlightAuthManager(refresh_fn=mock_fetch, ttl_seconds=3600.0)
        t1 = auth.get_token()
        self.assertEqual(t1, "token_v1")
        self.assertEqual(calls, 1)

        # Invalidate triggers fresh token on next fetch
        auth.invalidate()
        t2 = auth.get_token()
        self.assertEqual(t2, "token_v2")
        self.assertEqual(calls, 2)

    def test_webull_golden_fixture_contract_parsing(self):
        import json
        import os
        from datetime import datetime, timezone
        from src.leaps_scanner.data.webull import parse_webull_contracts_response

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "webull_chain_golden.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            golden_data = json.load(f)

        # Baseline date: 2024-01-01 to ensure 2026-12-18 has DTE > 250 and 2024-10-18 has DTE < 300
        ref_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candidates = parse_webull_contracts_response(golden_data, ref_date=ref_date)
        # 2 contracts expiring 2026-12-18 (DTE > 1000) should pass, 2024-10-18 (DTE ~291) also passes >= 250
        self.assertGreaterEqual(len(candidates), 2)
        for c in candidates:
            self.assertGreaterEqual(c.dte, 250.0)
            self.assertEqual(c.underlying, "AAPL")
            self.assertEqual(c.data_quality, "METADATA_ONLY")

    def test_fail_fast_opra_breaker(self):
        from src.leaps_scanner.data.webull import FailFastPermissionBreaker, PermissionDeniedOpraError
        breaker = FailFastPermissionBreaker()
        self.assertFalse(breaker.is_broken)

        # First 403 trip
        breaker.record_error(status_code=403, error_code="MARKET_DATA_NOT_SUBSCRIBED")
        self.assertTrue(breaker.is_broken)

        with self.assertRaises(PermissionDeniedOpraError):
            breaker.check_permission()

    def test_safe_pagination_iterator(self):
        from src.leaps_scanner.data.webull import SafePaginationIterator
        # Mock fetcher returning page with next key up to 3 pages
        def mock_fetch_page(key):
            if key is None:
                return [{"id": 1}], "page_2"
            elif key == "page_2":
                return [{"id": 2}], "page_3"
            elif key == "page_3":
                return [{"id": 3}], None
            return [], None

        iterator = SafePaginationIterator(fetch_page_fn=mock_fetch_page, max_pages=5)
        all_items = list(iterator)
        self.assertEqual(len(all_items), 3)
        self.assertEqual([it["id"] for it in all_items], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()

