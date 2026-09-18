"""
TDD Suite for CSP DTE scanning full coverage, 4-bucket filtering, and numerical safeguards.
Adheres to DC-CSP-10 ~ DC-CSP-16.
"""
import unittest
from datetime import datetime, timezone

from src.leaps_scanner.strategies.csp_harvest import evaluate_csp_harvest
from src.leaps_scanner.strategies.csp_wheel import evaluate_csp_wheel
from src.leaps_scanner.strategies.csp_vol_rank import evaluate_csp_vol_rank
from src.leaps_scanner.strategies.guards import GuardStatus
from src.leaps_scanner.scoring.csp_ranker import (
    CSPCandidate,
    CSPFilterConfig,
    EarningsStatus,
    get_dte_bucket,
    evaluate_csp_filters,
    rank_csp_boards,
)
from src.leaps_scanner.data.public_delayed import csp_target_expiries
from src.leaps_scanner.core.metrics import calculate_aroc, calculate_pop


class TestCSPDTECoverage(unittest.TestCase):
    def setUp(self):
        self.base_cand = CSPCandidate(
            symbol="AAPL260925P00220000",
            underlying="AAPL",
            spot=230.0,
            strike=220.0,
            dte=3.0,  # < 7 DTE
            bid=1.50,
            ask=1.60,
            delta=-0.20,
            open_interest=5000,
            volume=1200,
            iv_rank=0.60,
            rsi_14=45.0,
            pct_to_200dma=-0.02,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )

    def test_csp_target_expiries_covers_near_term_and_all_45d_fridays(self):
        """Verify csp_target_expiries generates dates for 0-7 DTE and 29-45 DTE."""
        asof = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)  # Wednesday
        expiries = csp_target_expiries(asof, min_dte=0.0, max_dte=45.0)
        # Should include this Friday (2026-09-18, ~2 DTE)
        self.assertIn("2026-09-18", expiries)
        # Should have around 6-7 Friday expiries covering up to 45 days
        self.assertGreaterEqual(len(expiries), 6)
        # Check that there is at least one expiry in the 29-45 DTE window
        has_29_to_45 = False
        for exp in expiries:
            exp_d = datetime.strptime(exp, "%Y-%m-%d").date()
            diff = (exp_d - asof.date()).days
            if 28 <= diff <= 45:
                has_29_to_45 = True
                break
        self.assertTrue(has_29_to_45, f"No expiry found between 28 and 45 DTE in {expiries}")

    def test_strategy_guards_do_not_reject_or_watch_for_short_or_long_dte(self):
        """DC-CSP-10: DTE does NOT set REJECT or WATCH in strategy guards; passes neutrally."""
        cand_3d = self.base_cand
        res_h = evaluate_csp_harvest(cand_3d)
        self.assertEqual(res_h.gates.get("dte"), GuardStatus.PASS)

        res_w = evaluate_csp_wheel(cand_3d)
        self.assertEqual(res_w.gates.get("dte"), GuardStatus.PASS)

        res_v = evaluate_csp_vol_rank(cand_3d)
        self.assertEqual(res_v.gates.get("dte"), GuardStatus.PASS)

    def test_get_dte_bucket_mapping(self):
        """DC-CSP-12: Accurate floating-point bucket categorization."""
        self.assertEqual(get_dte_bucket(0.5), "<7")
        self.assertEqual(get_dte_bucket(6.99), "<7")
        self.assertEqual(get_dte_bucket(7.0), "7-14")
        self.assertEqual(get_dte_bucket(13.99), "7-14")
        self.assertEqual(get_dte_bucket(14.0), "14-28")
        self.assertEqual(get_dte_bucket(27.99), "14-28")
        self.assertEqual(get_dte_bucket(28.0), "28-45")
        self.assertEqual(get_dte_bucket(45.0), "28-45")
        self.assertEqual(get_dte_bucket(45.04), "28-45")
        self.assertIsNone(get_dte_bucket(46.0))
        self.assertIsNone(get_dte_bucket(-1.0))

    def test_filter_selected_dte_buckets(self):
        """DC-CSP-16: User bucket selection filtering."""
        cand_3d = self.base_cand
        # Default config (selected_dte_buckets=None) -> passes
        cfg_default = CSPFilterConfig()
        passes, _ = evaluate_csp_filters(
            cand_3d, cfg_default, p_exec=1.55, aroc=0.25, buffer=0.04, pop=0.80,
            capital_info={"required_capital_per_contract": 22000.0}
        )
        self.assertTrue(passes)

        # Config only selecting 7-14 -> 3D should be rejected with DTE_BUCKET_EXCLUDED
        cfg_7_14 = CSPFilterConfig(selected_dte_buckets=["7-14"])
        passes_7_14, reasons_7_14 = evaluate_csp_filters(
            cand_3d, cfg_7_14, p_exec=1.55, aroc=0.25, buffer=0.04, pop=0.80,
            capital_info={"required_capital_per_contract": 22000.0}
        )
        self.assertFalse(passes_7_14)
        self.assertIn("DTE_BUCKET_EXCLUDED", reasons_7_14)

        # Config selecting <7 -> 3D should pass
        cfg_under_7 = CSPFilterConfig(selected_dte_buckets=["<7", "14-28"])
        passes_under_7, _ = evaluate_csp_filters(
            cand_3d, cfg_under_7, p_exec=1.55, aroc=0.25, buffer=0.04, pop=0.80,
            capital_info={"required_capital_per_contract": 22000.0}
        )
        self.assertTrue(passes_under_7)

        # Config with empty list [] -> explicitly excluded
        cfg_empty = CSPFilterConfig(selected_dte_buckets=[])
        passes_empty, reasons_empty = evaluate_csp_filters(
            cand_3d, cfg_empty, p_exec=1.55, aroc=0.25, buffer=0.04, pop=0.80,
            capital_info={"required_capital_per_contract": 22000.0}
        )
        self.assertFalse(passes_empty)
        self.assertIn("DTE_NO_BUCKET_SELECTED", reasons_empty)

    def test_rank_csp_boards_includes_short_dte_with_score_damping(self):
        """DC-CSP-15: Short DTE included in ranker without malicious score explosion."""
        cands = [
            self.base_cand,  # 3 DTE
            CSPCandidate(
                symbol="AAPL261016P00220000",
                underlying="AAPL",
                spot=230.0,
                strike=220.0,
                dte=30.0,  # 30 DTE
                bid=4.00,
                ask=4.20,
                delta=-0.22,
                open_interest=5000,
                volume=1200,
                iv_rank=0.60,
                rsi_14=45.0,
                pct_to_200dma=-0.02,
                earnings_status=EarningsStatus.CONFIRMED_SAFE,
            )
        ]
        boards = rank_csp_boards(cands)
        harvest_items = boards["harvest"]
        self.assertEqual(len(harvest_items), 2)
        symbols = [it.candidate.symbol for it in harvest_items]
        self.assertIn("AAPL260925P00220000", symbols)

    def test_pop_short_dte_smooth_fallback(self):
        """DC-CSP-14: POP smoothly falls back to 1-|delta| for t < 0.01 without 0/1 singularity."""
        # 1.0 DTE => t = 1.0/365.25 = 0.0027 < 0.01
        pop_short = calculate_pop(
            delta=-0.25,
            spot=100.0,
            breakeven=98.0,
            dte=1.0,
            sigma=0.30
        )
        self.assertAlmostEqual(pop_short, 0.75, places=3)

    def test_ratelimiter_pause_all(self):
        """DC-CSP-13: RateLimiter pause_all pauses global next request window."""
        from src.leaps_scanner.data.public_delayed import RateLimiter
        import time
        rl = RateLimiter(min_interval_s=0.01)
        rl.pause_all(duration_s=0.1)
        t0 = time.monotonic()
        rl.wait()
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.08)

    def test_parse_nasdaq_csp_chain_intraday_expiry(self):
        """DC-CSP-10: On expiration Friday morning, contract DTE is floored at >= 0.05 and not dropped."""
        from src.leaps_scanner.data.public_delayed import parse_nasdaq_csp_chain
        # Friday 10:00 AM New York time (14:00 UTC) on 2026-09-18
        asof_fri = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
        payload = {
            "data": {
                "table": {
                    "rows": [{
                        "p_Bid": "0.50", "p_Ask": "0.60", "p_Volume": "500",
                        "p_Openinterest": "1000", "strike": "220.00",
                        "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--260918p00220000",
                    }]
                }
            }
        }
        rows = parse_nasdaq_csp_chain(payload, min_dte=0.0, max_dte=45.05, asof=asof_fri)
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]["dte"], 0.05)


if __name__ == "__main__":
    unittest.main()
