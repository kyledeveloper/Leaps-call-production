import math
import unittest
from datetime import date, timedelta
from tests.python.conftest import block_network
from src.leaps_scanner.strategies.guards import GuardStatus


class TestPriceStoreAndOversoldStrategy(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_technical_indicators_calculation(self):
        from src.leaps_scanner.data.store.prices import (
            calculate_rsi,
            calculate_sma,
            calculate_realized_volatility,
            PriceBar,
            PriceStore
        )

        # 1. Test SMA
        prices = [10.0] * 50 + [20.0] * 50
        sma_20 = calculate_sma(prices, period=20)
        self.assertAlmostEqual(sma_20, 20.0)
        sma_100 = calculate_sma(prices, period=100)
        self.assertAlmostEqual(sma_100, 15.0)

        # 2. Test RSI
        # Constant prices should yield 50
        rsi_flat = calculate_rsi([100.0] * 30, period=14)
        self.assertAlmostEqual(rsi_flat, 50.0, places=1)

        # Monotonically increasing prices should yield 100
        rising = [100.0 + i for i in range(30)]
        rsi_rising = calculate_rsi(rising, period=14)
        self.assertGreater(rsi_rising, 90.0)

        # Monotonically falling prices should yield low RSI
        falling = [100.0 - i for i in range(30)]
        rsi_falling = calculate_rsi(falling, period=14)
        self.assertLess(rsi_falling, 15.0)

        # 3. Test Realized Volatility HV
        # Flat series has zero volatility
        hv_flat = calculate_realized_volatility([100.0] * 252, period=252)
        self.assertAlmostEqual(hv_flat, 0.0)

        # Sine-wave price series should yield positive volatility
        sine_prices = [100.0 + 10.0 * math.sin(i / 10.0) for i in range(260)]
        hv_sine = calculate_realized_volatility(sine_prices, period=252)
        self.assertGreater(hv_sine, 0.05)

    def test_price_store_insufficient_bars_guard(self):
        from src.leaps_scanner.data.store.prices import PriceBar, PriceStore
        store = PriceStore()
        # Add only 50 bars
        today = date(2026, 9, 15)
        bars = [
            PriceBar(
                trade_date=(today - timedelta(days=50 - i)).isoformat(),
                close=200.0 + i * 0.1,
                high=202.0 + i * 0.1,
                low=199.0 + i * 0.1,
                volume=1_000_000
            )
            for i in range(50)
        ]
        store.add_bars("AAPL", bars)
        metrics = store.get_metrics("AAPL")
        self.assertFalse(metrics.is_valid)
        self.assertIn("INSUFFICIENT_DAILY_BARS", metrics.reasons)
        self.assertEqual(metrics.bar_count, 50)

    def test_price_store_sufficient_bars(self):
        from src.leaps_scanner.data.store.prices import PriceBar, PriceStore
        store = PriceStore()
        today = date(2026, 9, 15)
        # Add 260 bars: first 240 at 220, then drop to 180, then slight bounce to 185
        bars = []
        for i in range(240):
            bars.append(PriceBar(
                trade_date=(today - timedelta(days=260 - i)).isoformat(),
                close=220.0,
                high=225.0,
                low=218.0,
                volume=1_000_000
            ))
        # 15 days dropping from 220 to 180
        for i in range(15):
            bars.append(PriceBar(
                trade_date=(today - timedelta(days=20 - i)).isoformat(),
                close=220.0 - (i + 1) * 2.66,
                high=222.0 - (i + 1) * 2.5,
                low=217.0 - (i + 1) * 2.7,
                volume=2_000_000
            ))
        # 5 days bouncing to 185
        for i in range(5):
            bars.append(PriceBar(
                trade_date=(today - timedelta(days=5 - i)).isoformat(),
                close=180.0 + (i + 1) * 1.0,
                high=186.0,
                low=179.0,
                volume=1_500_000
            ))

        store.add_bars("AAPL", bars)
        metrics = store.get_metrics("AAPL")
        self.assertTrue(metrics.is_valid)
        self.assertEqual(metrics.bar_count, 260)
        self.assertLess(metrics.rsi_14, 35.0)
        self.assertLess(metrics.pct_to_200dma, -0.10)
        self.assertGreater(metrics.drawdown_52w_high, 0.15)
        self.assertGreater(metrics.bounce_52w_low, 0.02)

    def test_oversold_strategy_core_universe_and_confluence(self):
        from src.leaps_scanner.strategies.oversold import (
            evaluate_oversold_underlying,
            evaluate_oversold_contract,
            OversoldUnderlyingMetrics
        )

        # 1. Non-core universe rejected
        metrics_penny = OversoldUnderlyingMetrics(
            symbol="UNKNOWN_XYZ",
            spot=5.0,
            rsi_14=20.0,
            pct_to_200dma=-0.20,
            drawdown_52w_high=0.30,
            bounce_52w_low=0.05,
            hv_20=0.30,
            bar_count=252,
            is_valid=True
        )
        res_penny = evaluate_oversold_underlying(metrics_penny)
        self.assertEqual(res_penny.status, GuardStatus.REJECT)
        self.assertIn("NOT_IN_CORE_UNIVERSE", res_penny.reasons)

        # 2. Insufficient bars rejected
        metrics_short = OversoldUnderlyingMetrics(
            symbol="AAPL",
            spot=185.0,
            rsi_14=25.0,
            pct_to_200dma=-0.12,
            drawdown_52w_high=0.18,
            bounce_52w_low=0.04,
            hv_20=0.25,
            bar_count=100,
            is_valid=False,
            reasons=["INSUFFICIENT_DAILY_BARS"]
        )
        res_short = evaluate_oversold_underlying(metrics_short)
        self.assertEqual(res_short.status, GuardStatus.REJECT)
        self.assertIn("INSUFFICIENT_DAILY_BARS", res_short.reasons)

        # 3. High confluence blue-chip -> PASS
        metrics_aapl = OversoldUnderlyingMetrics(
            symbol="AAPL",
            spot=185.0,
            rsi_14=26.0,          # Core: 1.0
            pct_to_200dma=-0.12,  # Core: 1.0
            drawdown_52w_high=0.18,# Core: 1.0
            bounce_52w_low=0.04,  # Core: 1.0
            hv_20=0.28,
            bar_count=252,
            is_valid=True
        )
        res_aapl = evaluate_oversold_underlying(metrics_aapl)
        self.assertEqual(res_aapl.status, GuardStatus.PASS)
        self.assertGreaterEqual(res_aapl.confluence_score, 2.0)
        self.assertGreaterEqual(res_aapl.core_signal_count, 1)

        # 4. Moderate confluence -> WATCH
        metrics_watch = OversoldUnderlyingMetrics(
            symbol="MSFT",
            spot=400.0,
            rsi_14=38.0,          # Weak: 0.5
            pct_to_200dma=-0.07,  # Weak: 0.5
            drawdown_52w_high=0.12,# Weak: 0.5
            bounce_52w_low=0.01,  # Weak: 0.5 (near low)
            hv_20=0.20,
            bar_count=252,
            is_valid=True
        )
        res_watch = evaluate_oversold_underlying(metrics_watch)
        self.assertEqual(res_watch.status, GuardStatus.WATCH)
        self.assertEqual(res_watch.confluence_score, 2.0)
        self.assertEqual(res_watch.core_signal_count, 0)

        # 5. Weak signals -> REJECT
        metrics_reject = OversoldUnderlyingMetrics(
            symbol="GOOGL",
            spot=170.0,
            rsi_14=55.0,
            pct_to_200dma=0.05,
            drawdown_52w_high=0.02,
            bounce_52w_low=0.25,
            hv_20=0.22,
            bar_count=252,
            is_valid=True
        )
        res_reject = evaluate_oversold_underlying(metrics_reject)
        self.assertEqual(res_reject.status, GuardStatus.REJECT)

        # 6. Contract level evaluation
        # Contract with passing underlying and passing liquidity
        c_pass = evaluate_oversold_contract(
            underlying_result=res_aapl,
            spot=185.0,
            strike=150.0,
            dte=350.0,
            p_exec=45.0,
            delta=0.82,
            liquidity_status=GuardStatus.PASS,
            days_to_earnings=30
        )
        self.assertEqual(c_pass.status, GuardStatus.PASS)

        # Contract with rejected liquidity
        c_bad_liq = evaluate_oversold_contract(
            underlying_result=res_aapl,
            spot=185.0,
            strike=150.0,
            dte=350.0,
            p_exec=45.0,
            delta=0.82,
            liquidity_status=GuardStatus.REJECT,
            days_to_earnings=30
        )
        self.assertEqual(c_bad_liq.status, GuardStatus.REJECT)
        self.assertIn("LIQUIDITY_REJECTED", c_bad_liq.reasons)

        # Contract with earnings window <= 10 days
        c_earnings = evaluate_oversold_contract(
            underlying_result=res_aapl,
            spot=185.0,
            strike=150.0,
            dte=350.0,
            p_exec=45.0,
            delta=0.82,
            liquidity_status=GuardStatus.PASS,
            days_to_earnings=5
        )
        self.assertIn("EVENT_WINDOW", c_earnings.reasons)


if __name__ == "__main__":
    unittest.main()
