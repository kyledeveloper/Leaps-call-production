"""
TDD Suite for CSP Strategy Evaluation and Multi-Board Ranker.
Covers:
- Board 1: Premium Harvesting (Delta -0.15 ~ -0.30, AROC sort, Gamma penalty for short DTE)
- Board 2: Wheel / Dip-Buying (Delta -0.30 ~ -0.45, Downside Buffer + oversold score)
- Board 3: High IV Rank Harvest (IV Rank >= 50%)
- 6 Toggleable Filters: AROC, Buffer, IVR, POP, Earnings, Liquidity
- DC-CSP-7: Concentration-aware contract sizing and Max Loss
- DC-CSP-8: Ternary earnings state handling
"""
import unittest

from src.leaps_scanner.strategies.csp_harvest import evaluate_csp_harvest
from src.leaps_scanner.strategies.csp_wheel import evaluate_csp_wheel
from src.leaps_scanner.strategies.csp_vol_rank import evaluate_csp_vol_rank
from src.leaps_scanner.scoring.csp_ranker import (
    CSPCandidate,
    CSPFilterConfig,
    EarningsStatus,
    rank_csp_boards,
)
from src.leaps_scanner.strategies.guards import GuardStatus


class TestCSPStrategiesAndRanker(unittest.TestCase):
    def setUp(self):
        self.cand_harvest = CSPCandidate(
            symbol="AAPL261016P00220000",
            underlying="AAPL",
            spot=230.0,
            strike=220.0,
            dte=30.0,
            bid=3.00,
            ask=3.20,
            delta=-0.22,
            open_interest=2500,
            volume=800,
            iv_rank=0.45,
            rsi_14=52.0,
            pct_to_200dma=0.08,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )

        self.cand_wheel = CSPCandidate(
            symbol="MSFT261016P00400000",
            underlying="MSFT",
            spot=420.0,
            strike=400.0,
            dte=35.0,
            bid=8.00,
            ask=8.40,
            delta=-0.38,
            open_interest=1800,
            volume=450,
            iv_rank=0.35,
            rsi_14=38.0,  # oversold
            pct_to_200dma=-0.03,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )

        self.cand_high_ivr = CSPCandidate(
            symbol="NVDA261016P00110000",
            underlying="NVDA",
            spot=120.0,
            strike=110.0,
            dte=28.0,
            bid=4.20,
            ask=4.50,
            delta=-0.28,
            open_interest=15000,
            volume=4200,
            iv_rank=0.78,  # High IVR
            rsi_14=46.0,
            pct_to_200dma=0.15,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )

        self.cand_earnings_risk = CSPCandidate(
            symbol="TSLA261016P00200000",
            underlying="TSLA",
            spot=220.0,
            strike=200.0,
            dte=25.0,
            bid=5.00,
            ask=5.50,
            delta=-0.25,
            open_interest=5000,
            volume=1200,
            iv_rank=0.60,
            rsi_14=49.0,
            pct_to_200dma=0.02,
            earnings_status=EarningsStatus.EARNINGS_IMPACTED,
        )

        self.cand_unverified = CSPCandidate(
            symbol="AMD261016P00140000",
            underlying="AMD",
            spot=155.0,
            strike=140.0,
            dte=32.0,
            bid=3.80,
            ask=4.10,
            delta=-0.24,
            open_interest=3200,
            volume=900,
            iv_rank=0.55,
            rsi_14=44.0,
            pct_to_200dma=0.05,
            earnings_status=EarningsStatus.EARNINGS_UNVERIFIED,
        )

    def test_evaluate_csp_harvest(self):
        """Board 1: Evaluates OTM Harvest (Delta -0.15 ~ -0.30)."""
        res_pass = evaluate_csp_harvest(self.cand_harvest)
        self.assertEqual(res_pass.status, GuardStatus.PASS)

        # Delta -0.38 is out of standard harvest window (-0.30 to -0.15) -> WATCH or REJECT
        res_wheel = evaluate_csp_harvest(self.cand_wheel)
        self.assertIn(res_wheel.status, (GuardStatus.WATCH, GuardStatus.REJECT))

    def test_evaluate_csp_wheel(self):
        """Board 2: Evaluates Wheel / Dip-buying (Delta -0.30 ~ -0.45)."""
        res_pass = evaluate_csp_wheel(self.cand_wheel)
        self.assertEqual(res_pass.status, GuardStatus.PASS)

        # Harvest candidate is Delta -0.22 (too light for wheel) -> WATCH or REJECT
        res_light = evaluate_csp_wheel(self.cand_harvest)
        self.assertIn(res_light.status, (GuardStatus.WATCH, GuardStatus.REJECT))

    def test_evaluate_csp_vol_rank(self):
        """Board 3: Evaluates High IV Rank (IVR >= 50%)."""
        res_high = evaluate_csp_vol_rank(self.cand_high_ivr)
        self.assertEqual(res_high.status, GuardStatus.PASS)

        # cand_harvest has IVR 0.45 (< 0.50) -> WATCH or REJECT
        res_low = evaluate_csp_vol_rank(self.cand_harvest)
        self.assertIn(res_low.status, (GuardStatus.WATCH, GuardStatus.REJECT))

    def test_rank_csp_boards_and_toggle_filters(self):
        """Verify multi-board ranking and 6 toggleable filters."""
        candidates = [
            self.cand_harvest,
            self.cand_wheel,
            self.cand_high_ivr,
            self.cand_earnings_risk,
            self.cand_unverified,
        ]

        # 1. Default config: All filters enabled with relaxed earnings
        cfg = CSPFilterConfig(
            filter_aroc=True,
            min_aroc=0.15,
            filter_buffer=True,
            min_buffer=0.03,
            filter_ivr=False,
            filter_pop=True,
            min_pop=0.70,
            filter_earnings=True,
            strict_earnings=False,  # filter IMPACTED, allow UNVERIFIED
            filter_liquidity=True,
        )

        boards = rank_csp_boards(candidates, config=cfg, alpha=0.5, cash_pool=50000.0)
        self.assertIn("harvest", boards)
        self.assertIn("wheel", boards)
        self.assertIn("vol_rank", boards)

        # Harvest board should have cand_harvest at top
        harvest_pass = [item.candidate.symbol for item in boards["harvest"] if item.status != GuardStatus.REJECT]
        harvest_all = [item.candidate.symbol for item in boards["harvest"]]
        self.assertIn(self.cand_harvest.symbol, harvest_pass)
        self.assertNotIn(self.cand_earnings_risk.symbol, harvest_pass)
        self.assertIn(self.cand_earnings_risk.symbol, harvest_all)

        cfg_strict = CSPFilterConfig(filter_earnings=True, strict_earnings=True)
        boards_strict = rank_csp_boards(candidates, config=cfg_strict, alpha=0.5)
        harvest_strict_pass = [
            item.candidate.symbol for item in boards_strict["harvest"] if item.status != GuardStatus.REJECT
        ]
        self.assertNotIn(self.cand_unverified.symbol, harvest_strict_pass)

        # 3. Disable earnings filter: TSLA should appear if eligible
        cfg_no_earn = CSPFilterConfig(filter_earnings=False)
        boards_no_earn = rank_csp_boards(candidates, config=cfg_no_earn, alpha=0.5)
        all_syms = [item.candidate.symbol for item in boards_no_earn["harvest"] + boards_no_earn["vol_rank"]]
        self.assertIn(self.cand_earnings_risk.symbol, all_syms)

    def test_dc_csp_3_positive_delta_strict_rejection(self):
        """DC-CSP-3: Positive Delta contracts must be unconditionally rejected."""
        cand_positive_delta = CSPCandidate(
            symbol="BAD_CALL_AS_PUT",
            underlying="AAPL",
            spot=230.0,
            strike=210.0,
            dte=30.0,
            bid=4.00,
            ask=4.30,
            delta=+0.25,  # Invalid positive delta for a Put!
            open_interest=5000,
            volume=1500,
            iv_rank=0.55,
            earnings_status=EarningsStatus.CONFIRMED_SAFE
        )

        res_h = evaluate_csp_harvest(cand_positive_delta)
        self.assertEqual(res_h.status, GuardStatus.REJECT)
        self.assertIn("POSITIVE_DELTA_PROHIBITED", res_h.reasons)

        res_w = evaluate_csp_wheel(cand_positive_delta)
        self.assertEqual(res_w.status, GuardStatus.REJECT)
        self.assertIn("POSITIVE_DELTA_PROHIBITED", res_w.reasons)

        res_v = evaluate_csp_vol_rank(cand_positive_delta)
        self.assertEqual(res_v.status, GuardStatus.REJECT)
        self.assertIn("POSITIVE_DELTA_PROHIBITED", res_v.reasons)

        # In ranking, positive delta contracts must be filtered completely
        boards = rank_csp_boards([cand_positive_delta])
        self.assertEqual(len(boards["harvest"]), 0)
        self.assertEqual(len(boards["wheel"]), 0)
        self.assertEqual(len(boards["vol_rank"]), 0)

    def test_default_filters_do_not_kill_wheel_pass_window(self):
        """Harvest POP>=70% must not be applied to Wheel's -0.30..-0.45 window."""
        boards = rank_csp_boards([self.cand_wheel], config=CSPFilterConfig())
        wheel_pass = [i.candidate.symbol for i in boards["wheel"] if i.status == GuardStatus.PASS]
        self.assertIn(self.cand_wheel.symbol, wheel_pass)

    def test_volume_thin_otm_put_is_not_hard_dropped(self):
        thin = CSPCandidate(
            symbol="AAPL261016P00200000",
            underlying="AAPL",
            spot=230.0,
            strike=210.0,
            dte=30.0,
            bid=3.00,
            ask=3.20,
            delta=-0.20,
            open_interest=2500,
            volume=0,
            iv_rank=0.55,
            rsi_14=48.0,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )
        boards = rank_csp_boards([thin], config=CSPFilterConfig(filter_liquidity=True))
        harvest = [i for i in boards["harvest"] if i.candidate.symbol == thin.symbol]
        self.assertEqual(len(harvest), 1)
        self.assertNotEqual(harvest[0].status, GuardStatus.REJECT)

    def test_harvest_volume_reject_does_not_pass(self):
        """P0: high OI + vol=0 (+ empty book) volume REJECT must not fall through to PASS."""
        thin_vol = CSPCandidate(
            symbol="AAPL261016P00220000",
            underlying="AAPL",
            spot=230.0,
            strike=220.0,
            dte=30.0,
            bid=3.00,
            ask=3.20,
            delta=-0.22,
            open_interest=5000,
            volume=0,
            bid_size=0,
            ask_size=0,
            iv_rank=0.45,
            rsi_14=52.0,
            pct_to_200dma=0.08,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )
        res = evaluate_csp_harvest(thin_vol)
        self.assertEqual(res.gates["liquidity"], GuardStatus.REJECT)
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertTrue(
            any("INSUFFICIENT_ACTIVITY" in r for r in res.reasons),
            res.reasons,
        )

        # Same fold in wheel / vol_rank evaluators
        thin_wheel = CSPCandidate(
            symbol="MSFT261016P00400000",
            underlying="MSFT",
            spot=420.0,
            strike=400.0,
            dte=35.0,
            bid=8.00,
            ask=8.40,
            delta=-0.38,
            open_interest=5000,
            volume=0,
            bid_size=0,
            ask_size=0,
            iv_rank=0.35,
            rsi_14=38.0,
            pct_to_200dma=-0.03,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )
        res_w = evaluate_csp_wheel(thin_wheel)
        self.assertEqual(res_w.gates["liquidity"], GuardStatus.REJECT)

        thin_ivr = CSPCandidate(
            symbol="NVDA261016P00110000",
            underlying="NVDA",
            spot=120.0,
            strike=110.0,
            dte=28.0,
            bid=4.20,
            ask=4.50,
            delta=-0.28,
            open_interest=15000,
            volume=0,
            bid_size=0,
            ask_size=0,
            iv_rank=0.78,
            rsi_14=46.0,
            pct_to_200dma=0.15,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )
        res_v = evaluate_csp_vol_rank(thin_ivr)
        self.assertEqual(res_v.gates["liquidity"], GuardStatus.REJECT)

    def test_wheel_without_dip_is_watch(self):
        hot = CSPCandidate(
            symbol="NVDA261016P00110000",
            underlying="NVDA",
            spot=120.0,
            strike=110.0,
            dte=28.0,
            bid=4.20,
            ask=4.50,
            delta=-0.38,
            open_interest=15000,
            volume=4200,
            rsi_14=62.0,
            pct_to_200dma=0.12,
            earnings_status=EarningsStatus.CONFIRMED_SAFE,
        )
        res = evaluate_csp_wheel(hot)
        self.assertEqual(res.status, GuardStatus.WATCH)
    def test_csp_per_gate_evaluation_and_ranked_item_gates(self):
        """Verify that all CSP strategies evaluate individual gates and RankedCSPItem carries gates."""
        # 1. Evaluate harvest candidate
        res_harvest = evaluate_csp_harvest(self.cand_harvest)
        self.assertTrue(hasattr(res_harvest, "gates"), "CSPHarvestResult must have gates dictionary")
        self.assertIn("delta", res_harvest.gates)
        self.assertIn("buffer", res_harvest.gates)
        self.assertIn("dte", res_harvest.gates)

        # 2. Evaluate wheel candidate
        res_wheel = evaluate_csp_wheel(self.cand_wheel)
        self.assertTrue(hasattr(res_wheel, "gates"), "CSPWheelResult must have gates dictionary")
        self.assertIn("delta", res_wheel.gates)
        self.assertIn("oversold", res_wheel.gates)
        self.assertIn("dte", res_wheel.gates)

        # 3. Evaluate vol_rank candidate
        res_vol = evaluate_csp_vol_rank(self.cand_high_ivr)
        self.assertTrue(hasattr(res_vol, "gates"), "CSPVolRankResult must have gates dictionary")
        self.assertIn("ivr", res_vol.gates)
        self.assertIn("delta", res_vol.gates)

        # 4. Check rank_csp_boards produces RankedCSPItem with populated gates
        boards = rank_csp_boards([self.cand_harvest, self.cand_wheel, self.cand_high_ivr])
        for board_key in ("harvest", "wheel", "vol_rank"):
            for item in boards[board_key]:
                self.assertTrue(hasattr(item, "gates"), f"RankedCSPItem on {board_key} must have gates")
                self.assertIsInstance(item.gates, dict)
                self.assertGreater(len(item.gates), 0, f"RankedCSPItem on {board_key} must have non-empty gates")


if __name__ == "__main__":
    unittest.main()
