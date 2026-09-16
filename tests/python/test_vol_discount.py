import unittest
from datetime import date, timedelta
from tests.python.conftest import block_network
from src.leaps_scanner.strategies.guards import GuardStatus


class TestVolDiscountAndIVStore(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_iv_history_store_degradation_guard(self):
        from src.leaps_scanner.data.store.iv_history import IVHistoryStore, IVDataPoint
        store = IVHistoryStore()

        # Less than 90 days -> Degraded
        today = date(2026, 9, 15)
        for i in range(50):
            d = (today - timedelta(days=50 - i)).isoformat()
            store.add_data_point("AAPL", IVDataPoint(trade_date=d, atm_iv=0.25 + 0.001 * i))

        metrics = store.get_metrics("AAPL", current_iv=0.27)
        self.assertTrue(metrics.is_degraded)
        self.assertIn("STRATEGY_DEGRADED_INSUFFICIENT_HISTORY", metrics.reasons)
        self.assertEqual(metrics.valid_days, 50)

    def test_iv_history_store_percentile_and_rank(self):
        from src.leaps_scanner.data.store.iv_history import IVHistoryStore, IVDataPoint
        store = IVHistoryStore()

        today = date(2026, 9, 15)
        # 200 days with IVs uniformly distributed between 0.20 and 0.40
        for i in range(200):
            d = (today - timedelta(days=200 - i)).isoformat()
            iv = 0.20 + 0.20 * (i / 199.0)
            store.add_data_point("AAPL", IVDataPoint(trade_date=d, atm_iv=iv))

        # Current IV = 0.22 -> should be near 10th percentile and rank 0.10
        metrics = store.get_metrics("AAPL", current_iv=0.22)
        self.assertFalse(metrics.is_degraded)
        self.assertEqual(metrics.valid_days, 200)
        self.assertAlmostEqual(metrics.iv_percentile, 0.10, places=1)
        self.assertAlmostEqual(metrics.iv_rank, 0.10, places=1)
        self.assertLess(metrics.iv_z_score, -1.0)

    def test_regime_a_bottoming_discount(self):
        from src.leaps_scanner.strategies.vol_discount import (
            evaluate_vol_discount_underlying,
            evaluate_vol_discount_contract,
            VolDiscountUnderlyingMetrics
        )

        # 1. Regime A Pass
        metrics_a_pass = VolDiscountUnderlyingMetrics(
            symbol="AAPL",
            spot=220.0,
            pct_change_20d=-0.04,
            drawdown_52w_high=0.08,
            current_atm_iv=0.21,
            hv_252=0.26,
            iv_percentile=0.15,
            iv_rank=0.14,
            iv_z_score=-1.2,
            valid_history_days=220,
            is_degraded=False
        )
        res_pass = evaluate_vol_discount_underlying(metrics_a_pass)
        self.assertEqual(res_pass.status, GuardStatus.PASS)
        self.assertEqual(res_pass.regime, "REGIME_A")

        # 2. Regime A Watch (IV percentile 25%)
        metrics_a_watch = VolDiscountUnderlyingMetrics(
            symbol="AAPL",
            spot=220.0,
            pct_change_20d=-0.04,
            drawdown_52w_high=0.08,
            current_atm_iv=0.24,
            hv_252=0.26,
            iv_percentile=0.25,
            iv_rank=0.25,
            iv_z_score=-0.5,
            valid_history_days=220,
            is_degraded=False
        )
        res_watch = evaluate_vol_discount_underlying(metrics_a_watch)
        self.assertEqual(res_watch.status, GuardStatus.WATCH)
        self.assertEqual(res_watch.regime, "REGIME_A")

        # 3. Regime A Reject (High IV)
        metrics_a_reject = VolDiscountUnderlyingMetrics(
            symbol="AAPL",
            spot=220.0,
            pct_change_20d=-0.04,
            drawdown_52w_high=0.08,
            current_atm_iv=0.35,
            hv_252=0.26,
            iv_percentile=0.55,
            iv_rank=0.55,
            iv_z_score=0.8,
            valid_history_days=220,
            is_degraded=False
        )
        res_reject = evaluate_vol_discount_underlying(metrics_a_reject)
        self.assertEqual(res_reject.status, GuardStatus.REJECT)

    def test_regime_b_post_crash_relative_value(self):
        from src.leaps_scanner.strategies.vol_discount import (
            evaluate_vol_discount_underlying,
            VolDiscountUnderlyingMetrics
        )

        # Severe drop: 20d change -20%, 52w drawdown 25%
        # IV percentile 35% (<40%) -> Pass for Regime B even though not <20%
        metrics_b_pass = VolDiscountUnderlyingMetrics(
            symbol="NVDA",
            spot=100.0,
            pct_change_20d=-0.22,
            drawdown_52w_high=0.28,
            current_atm_iv=0.42,
            hv_252=0.50,
            iv_percentile=0.35,
            iv_rank=0.35,
            iv_z_score=-0.3,
            valid_history_days=200,
            is_degraded=False
        )
        res_b = evaluate_vol_discount_underlying(metrics_b_pass)
        self.assertEqual(res_b.status, GuardStatus.PASS)
        self.assertEqual(res_b.regime, "REGIME_B")

        # Severe drop but IV spiked into panic (>40% percentile)
        metrics_b_panic = VolDiscountUnderlyingMetrics(
            symbol="NVDA",
            spot=100.0,
            pct_change_20d=-0.25,
            drawdown_52w_high=0.30,
            current_atm_iv=0.75,
            hv_252=0.50,
            iv_percentile=0.85,
            iv_rank=0.85,
            iv_z_score=2.1,
            valid_history_days=200,
            is_degraded=False
        )
        res_b_panic = evaluate_vol_discount_underlying(metrics_b_panic)
        self.assertEqual(res_b_panic.status, GuardStatus.REJECT)

    def test_vol_discount_contract_evaluation(self):
        from src.leaps_scanner.strategies.vol_discount import (
            evaluate_vol_discount_underlying,
            evaluate_vol_discount_contract,
            VolDiscountUnderlyingMetrics
        )

        metrics = VolDiscountUnderlyingMetrics(
            symbol="AAPL",
            spot=220.0,
            pct_change_20d=-0.04,
            drawdown_52w_high=0.08,
            current_atm_iv=0.21,
            hv_252=0.26,
            iv_percentile=0.15,
            iv_rank=0.14,
            iv_z_score=-1.2,
            valid_history_days=220,
            is_degraded=False
        )
        underlying_res = evaluate_vol_discount_underlying(metrics)

        # 1. Valid contract
        c_ok = evaluate_vol_discount_contract(
            underlying_result=underlying_res,
            spot=220.0,
            strike=200.0,
            dte=350.0,
            p_exec=35.0,
            delta=0.65,
            liquidity_status=GuardStatus.PASS,
            days_to_earnings=45
        )
        self.assertEqual(c_ok.status, GuardStatus.PASS)

        # 2. Strike out of window: K in [0.70S, 1.25S] -> strike 130 is 0.59S (too deep ITM for Vega play)
        c_deep = evaluate_vol_discount_contract(
            underlying_result=underlying_res,
            spot=220.0,
            strike=130.0,
            dte=350.0,
            p_exec=95.0,
            delta=0.92,
            liquidity_status=GuardStatus.PASS
        )
        self.assertEqual(c_deep.status, GuardStatus.REJECT)
        self.assertIn("STRIKE_OUT_OF_WINDOW", c_deep.reasons[0])

        # 3. Earnings within 10 days in Regime A -> downgrades to WATCH
        c_earnings = evaluate_vol_discount_contract(
            underlying_result=underlying_res,
            spot=220.0,
            strike=200.0,
            dte=350.0,
            p_exec=35.0,
            delta=0.65,
            liquidity_status=GuardStatus.PASS,
            days_to_earnings=6
        )
        self.assertEqual(c_earnings.status, GuardStatus.WATCH)
        self.assertIn("EVENT_WINDOW", c_earnings.reasons)

    def test_realized_vol_proxy_caps_at_watch(self):
        from src.leaps_scanner.strategies.vol_discount import (
            evaluate_vol_discount_underlying,
            VolDiscountUnderlyingMetrics,
        )
        metrics = VolDiscountUnderlyingMetrics(
            symbol="SPY",
            spot=550.0,
            pct_change_20d=-0.03,
            drawdown_52w_high=0.06,
            current_atm_iv=0.12,
            hv_252=0.18,
            iv_percentile=0.0,
            iv_rank=0.0,
            iv_z_score=0.0,
            valid_history_days=12,
            is_degraded=True,
            hv_20=0.12,
            hv_percentile=0.12,
            hv_z_score=-1.4,
        )
        res = evaluate_vol_discount_underlying(metrics)
        self.assertEqual(res.status, GuardStatus.WATCH)
        self.assertEqual(res.regime, "REGIME_A_HV")
        self.assertIn("REALIZED_VOL_PROXY", res.reasons)
        self.assertNotEqual(res.status, GuardStatus.PASS)

    def test_degraded_without_hv_still_rejects(self):
        from src.leaps_scanner.strategies.vol_discount import (
            evaluate_vol_discount_underlying,
            VolDiscountUnderlyingMetrics,
        )
        metrics = VolDiscountUnderlyingMetrics(
            symbol="AAPL",
            spot=220.0,
            pct_change_20d=-0.04,
            drawdown_52w_high=0.08,
            current_atm_iv=0.21,
            hv_252=0.26,
            iv_percentile=0.0,
            iv_rank=0.0,
            iv_z_score=0.0,
            valid_history_days=12,
            is_degraded=True,
        )
        res = evaluate_vol_discount_underlying(metrics)
        self.assertEqual(res.status, GuardStatus.REJECT)
        self.assertEqual(res.regime, "NONE")

    def test_iv_store_persists_roundtrip(self):
        import os
        import tempfile
        from datetime import date, timedelta
        from src.leaps_scanner.data.store.iv_history import IVHistoryStore, IVDataPoint
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            store = IVHistoryStore(persist_path=path)
            today = date(2026, 9, 15)
            for i in range(100):
                d = (today - timedelta(days=100 - i)).isoformat()
                store.add_data_point("QQQ", IVDataPoint(trade_date=d, atm_iv=0.18 + 0.001 * i))
            store.flush()
            reloaded = IVHistoryStore(persist_path=path)
            metrics = reloaded.get_metrics("QQQ", current_iv=0.185)
            self.assertFalse(metrics.is_degraded)
            self.assertEqual(metrics.valid_days, 100)
        finally:
            os.remove(path)

    def test_ranker_uses_hv_proxy_when_iv_history_missing(self):
        from src.leaps_scanner.scoring.ranker import StrategyCandidate, MemoryRanker
        cand = StrategyCandidate(
            symbol="SPY270115C00450000",
            underlying="SPY",
            strike=450.0,
            spot=550.0,
            dte=400.0,
            bid=108.0,
            ask=110.0,
            delta=0.70,
            open_interest=2000,
            volume=80,
            iv=0.16,
            iv_percentile=None,
            hv_252=0.18,
            hv_20=0.12,
            hv_percentile=0.14,
            hv_z_score=-1.1,
            iv_history_days=5,
            valid_history_days=252,
        )
        boards = MemoryRanker([cand]).rank_boards(alpha=0.5)
        item = boards["vol_discount"][0]
        self.assertEqual(item.status, GuardStatus.WATCH)
        self.assertEqual(item.regime, "REGIME_A_HV")
        self.assertNotIn("MISSING_IV_PERCENTILE", item.reasons)


if __name__ == "__main__":
    unittest.main()
