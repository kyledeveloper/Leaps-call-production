import json
import threading
import time
import unittest
from datetime import datetime, timezone

from tests.python.conftest import block_network
from src.leaps_scanner.data.public_delayed import (
    parse_occ_from_nasdaq_url,
    parse_nasdaq_chain,
    parse_nasdaq_last_trade,
    parse_yahoo_chart,
    occ_symbol,
    PublicDelayedClient,
)
from src.leaps_scanner.data.store.daily_bars import DailyBarCache
from src.leaps_scanner.api.server import AppState, create_api_handler_class


NASDAQ_FIXTURE = {
    "data": {
        "lastTrade": "LAST TRADE: $331.34 (AS OF SEP 15, 2026)",
        "table": {
            "rows": [
                {
                    "c_Bid": "68.10",
                    "c_Ask": "70.40",
                    "c_Volume": "12",
                    "c_Openinterest": "1500",
                    "strike": "270.00",
                    "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--280121c00270000",
                },
                {
                    "c_Bid": "1.00",
                    "c_Ask": "1.20",
                    "c_Volume": "--",
                    "c_Openinterest": "9",
                    "strike": "400.00",
                    "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261218c00400000",
                },
            ]
        },
    }
}

YAHOO_CHART_FIXTURE = {
    "chart": {
        "result": [{
            "meta": {"regularMarketPrice": 331.34, "symbol": "AAPL"},
            "timestamp": [1726358400 + 86400 * i for i in range(220)],
            "indicators": {
                "quote": [{
                    "close": [300.0 + (i % 7) for i in range(220)],
                    "high": [302.0 + (i % 7) for i in range(220)],
                    "low": [298.0 + (i % 7) for i in range(220)],
                    "open": [300.0 + (i % 7) for i in range(220)],
                    "volume": [1e7] * 220,
                }]
            },
            "events": {"dividends": {"1": {"date": int(__import__("time").time()) - 30 * 86400, "amount": 0.25}}},
        }]
    }
}


class TestPublicDelayedParsers(unittest.TestCase):
    def test_occ_and_symbol(self):
        parsed = parse_occ_from_nasdaq_url(
            "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--280121c00250000"
        )
        self.assertEqual(parsed[0], "AAPL")
        self.assertEqual(parsed[1], "2028-01-21")
        self.assertEqual(parsed[2], "C")
        self.assertEqual(parsed[3], 250.0)
        self.assertEqual(occ_symbol("AAPL", "2028-01-21", 250.0), "AAPL280121C00250000")
        etf = parse_occ_from_nasdaq_url(
            "/market-activity/etf/spy/option-chain/call-put-options/spy---270617c00050000"
        )
        self.assertEqual(etf[0], "SPY")
        self.assertEqual(etf[1], "2027-06-17")
        self.assertEqual(etf[3], 50.0)

    def test_nasdaq_keeps_leaps_drops_short_dated(self):
        asof = datetime(2026, 9, 15, tzinfo=timezone.utc)
        rows = parse_nasdaq_chain(NASDAQ_FIXTURE, min_dte=250.0, asof=asof)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["expiry"], "2028-01-21")
        self.assertGreater(rows[0]["dte"], 250)
        self.assertEqual(rows[0]["bid"], 68.10)
        self.assertEqual(parse_nasdaq_last_trade(NASDAQ_FIXTURE["data"]["lastTrade"]), 331.34)

    def test_yahoo_chart_bars_and_div(self):
        spot, bars, div_yield = parse_yahoo_chart(YAHOO_CHART_FIXTURE)
        self.assertAlmostEqual(spot, 331.34)
        self.assertEqual(len(bars), 220)
        self.assertGreater(div_yield, 0.0)

    def test_client_with_injected_fetch(self):
        def fake_fetch(url, headers):
            if "finance.yahoo.com" in url or "query1.finance.yahoo.com" in url:
                return 200, json.dumps(YAHOO_CHART_FIXTURE).encode()
            if "nasdaq.com" in url:
                return 200, json.dumps(NASDAQ_FIXTURE).encode()
            return 404, b""

        client = PublicDelayedClient(fetch_fn=fake_fetch)
        cands = client.get_leaps_candidates(["AAPL"])
        self.assertGreaterEqual(len(cands), 1)
        self.assertEqual(cands[0].data_quality, "DELAYED")
        self.assertEqual(cands[0].underlying, "AAPL")
        self.assertGreater(cands[0].dte, 250)

    def test_delayed_mode_hermetic_does_not_crash(self):
        block_network()
        state = AppState(offline_mode=True)
        handler = create_api_handler_class(state)
        code, _, body = handler.dispatch(
            "POST", "/api/v1/mode", json.dumps({"source": "delayed"}).encode()
        )
        self.assertIn(code, (200, 202))
        res = json.loads(body.decode())
        self.assertEqual(res["source"], "delayed")

    def test_daily_bar_cache_skips_yahoo_on_second_scan(self):
        calls = []

        def fake_fetch(url, headers):
            calls.append(url)
            if "finance.yahoo.com" in url or "query1.finance.yahoo.com" in url:
                return 200, json.dumps(YAHOO_CHART_FIXTURE).encode()
            if "nasdaq.com" in url:
                return 200, json.dumps(NASDAQ_FIXTURE).encode()
            return 404, b""

        cache = DailyBarCache(session_date_fn=lambda: "2026-09-16")
        client = PublicDelayedClient(
            fetch_fn=fake_fetch, bar_cache=cache, max_workers=1, min_interval_s=0
        )
        first = client.get_leaps_candidates(["AAPL", "MSFT"])
        self.assertGreaterEqual(len(first), 1)
        yahoo_first = sum(1 for u in calls if "finance.yahoo.com" in u)
        nasdaq_first = sum(1 for u in calls if "nasdaq.com" in u)
        self.assertEqual(yahoo_first, 2)
        self.assertGreaterEqual(nasdaq_first, 2)

        calls.clear()
        second = client.get_leaps_candidates(["AAPL", "MSFT"])
        self.assertGreaterEqual(len(second), 1)
        yahoo_second = sum(1 for u in calls if "finance.yahoo.com" in u)
        nasdaq_second = sum(1 for u in calls if "nasdaq.com" in u)
        self.assertEqual(yahoo_second, 0)
        self.assertGreaterEqual(nasdaq_second, 2)

    def test_controlled_concurrency_runs_symbols_in_parallel(self):
        lock = threading.Lock()
        in_flight = 0
        max_flight = 0

        def fake_fetch(url, headers):
            nonlocal in_flight, max_flight
            with lock:
                in_flight += 1
                max_flight = max(max_flight, in_flight)
            time.sleep(0.05)
            with lock:
                in_flight -= 1
            if "finance.yahoo.com" in url or "query1.finance.yahoo.com" in url:
                return 200, json.dumps(YAHOO_CHART_FIXTURE).encode()
            if "nasdaq.com" in url:
                return 200, json.dumps(NASDAQ_FIXTURE).encode()
            return 404, b""

        client = PublicDelayedClient(fetch_fn=fake_fetch, max_workers=4, min_interval_s=0)
        names = ["AAPL", "MSFT", "NVDA", "AMZN"]
        t0 = time.monotonic()
        cands = client.get_leaps_candidates(names)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(len(cands), 1)
        self.assertGreaterEqual(max_flight, 2)
        # Sequential 4 names * 2 HTTP * 50ms = 400ms. Parallel should beat that.
        self.assertLess(elapsed, 0.32)

    def test_progress_callback_counts_all_symbols(self):
        def fake_fetch(url, headers):
            if "finance.yahoo.com" in url or "query1.finance.yahoo.com" in url:
                return 200, json.dumps(YAHOO_CHART_FIXTURE).encode()
            if "nasdaq.com" in url:
                return 200, json.dumps(NASDAQ_FIXTURE).encode()
            return 404, b""

        seen = []
        client = PublicDelayedClient(fetch_fn=fake_fetch, max_workers=3, min_interval_s=0)

        def on_progress(done, total, sym, batch):
            seen.append((done, total, sym, len(batch)))

        client.get_leaps_candidates(["AAPL", "MSFT", "NVDA"], progress_cb=on_progress)
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[-1][0], 3)
        self.assertEqual(seen[-1][1], 3)


if __name__ == "__main__":
    unittest.main()
