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


if __name__ == "__main__":
    unittest.main()
