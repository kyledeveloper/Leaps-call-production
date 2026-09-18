"""
TDD Suite for Cash Secured Put (CSP) Pricing, Greeks, IV Solver, and Metrics.
Enforces Defensive Clauses:
- DC-CSP-1: Safe American Put pricing, symmetry & physical bounds
- DC-CSP-3: Put negative Delta clamping and hermetic Greeks
- DC-CSP-4: DTE hard floor, ROC vs AROC separation
- DC-CSP-5: POP and -15% downside stress testing
- DC-CSP-6: Zero-bid rejection & sell-side P_exec
- DC-CSP-7: Concentration-capped contract recommendations & Max Loss
"""
import math
import unittest

from src.leaps_scanner.core.american_pricing import (
    black_scholes_call,
    black_scholes_put,
    bjerksund_stensland_2002,
    bjerksund_stensland_put,
)
from src.leaps_scanner.core.greeks import (
    AmericanGreeks,
    calculate_american_put_greeks,
    calculate_put_fallback_delta,
)
from src.leaps_scanner.core.iv_solver import (
    solve_american_put_iv,
)
from src.leaps_scanner.core.metrics import (
    calculate_sell_pexec,
    calculate_aroc,
    calculate_roc,
    calculate_downside_buffer,
    calculate_pop,
    calculate_csp_capital_allocation,
    calculate_stress_test_pnl,
)
from src.leaps_scanner.strategies.guards import (
    evaluate_liquidity_guard,
    GuardStatus,
)


class TestCSPPricingAndMetrics(unittest.TestCase):
    def test_black_scholes_put_basic_and_parity(self):
        """Verify Black-Scholes put pricing satisfies put-call parity."""
        spot = 100.0
        strike = 100.0
        t = 30.0 / 365.25
        r = 0.05
        q = 0.01
        sigma = 0.25

        c = black_scholes_call(spot, strike, t, r, q, sigma)
        p = black_scholes_put(spot, strike, t, r, q, sigma)

        # Put-Call Parity: C - P = S * exp(-q*t) - K * exp(-r*t)
        lhs = c - p
        rhs = spot * math.exp(-q * t) - strike * math.exp(-r * t)
        self.assertAlmostEqual(lhs, rhs, places=5)

    def test_bjerksund_stensland_put_physical_bounds(self):
        """DC-CSP-1: Verify American Put bounds: max(0, K-S, P_eur) <= P_amer <= K."""
        test_cases = [
            # spot, strike, t, r, q, sigma
            (100.0, 90.0, 30.0 / 365.25, 0.04, 0.01, 0.25),   # OTM
            (100.0, 100.0, 45.0 / 365.25, 0.05, 0.02, 0.30),  # ATM
            (80.0, 100.0, 60.0 / 365.25, 0.05, 0.0, 0.25),    # Deep ITM
            (50.0, 100.0, 14.0 / 365.25, 0.08, 0.05, 0.40),   # Very deep ITM
            (100.0, 95.0, 30.0 / 365.25, 0.0, 0.0, 0.20),     # Zero rates (r=0, q=0)
            (100.0, 95.0, 30.0 / 365.25, 0.05, 0.0, 0.20),    # Non-dividend stock (q=0)
        ]

        for spot, strike, t, r, q, sigma in test_cases:
            p_amer = bjerksund_stensland_put(spot, strike, t, r, q, sigma)
            p_eur = black_scholes_put(spot, strike, t, r, q, sigma)
            intrinsic = max(0.0, strike - spot)

            # Lower bound: max(intrinsic, p_eur)
            self.assertGreaterEqual(p_amer, intrinsic - 1e-6)
            self.assertGreaterEqual(p_amer, p_eur - 1e-6)
            # Upper bound: strike K
            self.assertLessEqual(p_amer, strike + 1e-6)

    def test_put_greeks_and_clamping(self):
        """DC-CSP-3: Put delta must strictly be in [-1.0, 0.0], Gamma >= 0."""
        spot = 100.0
        strike = 95.0
        t = 30.0 / 365.25
        r = 0.04
        q = 0.01
        sigma = 0.25

        greeks = calculate_american_put_greeks(spot, strike, t, r, q, sigma)
        self.assertIsInstance(greeks, AmericanGreeks)
        self.assertLessEqual(greeks.delta, 0.0)
        self.assertGreaterEqual(greeks.delta, -1.0)
        self.assertGreaterEqual(greeks.gamma, 0.0)
        # Put theta is negative for long put; seller captures positive decay
        self.assertLess(greeks.theta, 0.0)

    def test_put_fallback_delta(self):
        """DC-CSP-3: Fallback delta for put must always be negative."""
        # Deep OTM Put (spot >> strike): delta close to 0 (e.g. -0.05 to -0.15)
        d_otm = calculate_put_fallback_delta(spot=120.0, strike=100.0)
        self.assertLessEqual(d_otm, 0.0)
        self.assertGreaterEqual(d_otm, -0.5)

        # Deep ITM Put (spot << strike): delta close to -1.0
        d_itm = calculate_put_fallback_delta(spot=70.0, strike=100.0)
        self.assertLessEqual(d_itm, -0.5)
        self.assertGreaterEqual(d_itm, -1.0)

    def test_solve_american_put_iv(self):
        """Verify Brent IV solver recovers Put implied volatility."""
        spot = 100.0
        strike = 95.0
        t = 30.0 / 365.25
        r = 0.04
        q = 0.01
        known_sigma = 0.28

        true_price = bjerksund_stensland_put(spot, strike, t, r, q, known_sigma)
        res = solve_american_put_iv(spot, strike, t, r, q, true_price)
        self.assertEqual(res.status, "CONVERGED")
        self.assertIsNotNone(res.iv)
        self.assertAlmostEqual(res.iv, known_sigma, places=3)

    def test_sell_pexec_and_zero_bid(self):
        """DC-CSP-6: Sell-side P_exec formula and zero-bid rejection."""
        # Standard bid/ask
        bid = 1.80
        ask = 2.20
        # mid = 2.00. alpha=0.5 -> p_exec = 2.00 - 0.5 * (2.00 - 1.80) = 1.90
        p_exec = calculate_sell_pexec(bid, ask, alpha=0.5)
        self.assertAlmostEqual(p_exec, 1.90, places=4)

        # alpha=0.0 -> mid (2.00), alpha=1.0 -> bid (1.80)
        self.assertAlmostEqual(calculate_sell_pexec(bid, ask, alpha=0.0), 2.00)
        self.assertAlmostEqual(calculate_sell_pexec(bid, ask, alpha=1.0), 1.80)

        # Zero or negative bid must be rejected
        guard = evaluate_liquidity_guard(bid=0.0, ask=0.50, open_interest=100, volume=50)
        self.assertEqual(guard.status, GuardStatus.REJECT)

    def test_aroc_and_roc_with_dte_guard(self):
        """DC-CSP-4: AROC hard floor and ROC calculation."""
        # 30 DTE, Strike 100, P_exec 2.00
        # ROC = 2.00 / 100 = 2.0%
        # AROC = 2.0% * (365 / 30) = 24.333%
        roc = calculate_roc(pexec=2.0, strike=100.0)
        self.assertAlmostEqual(roc, 0.02, places=4)

        aroc = calculate_aroc(pexec=2.0, strike=100.0, dte=30.0)
        self.assertAlmostEqual(aroc, 0.02 * (365.0 / 30.0), places=4)

        # DC-CSP-15: DTE < 1 must floor to 1.0 to prevent division by zero
        aroc_short = calculate_aroc(pexec=0.5, strike=100.0, dte=0.5)
        # Should be floored at dte=1.0
        self.assertAlmostEqual(aroc_short, (0.5 / 100.0) * (365.0 / 1.0), places=4)

    def test_downside_buffer(self):
        """Verify downside buffer calculation."""
        # Spot 100, Strike 90 -> 10% buffer
        buffer = calculate_downside_buffer(spot=100.0, strike=90.0)
        self.assertAlmostEqual(buffer, 0.10, places=4)

    def test_pop_prefers_breakeven_over_delta(self):
        """Short-put POP is P(S_T > break-even), not 1-|Delta|."""
        delta_only = calculate_pop(delta=-0.40)
        self.assertAlmostEqual(delta_only, 0.60, places=4)
        be_pop = calculate_pop(
            delta=-0.40,
            spot=100.0,
            breakeven=94.0,
            dte=30.0,
            sigma=0.25,
        )
        self.assertGreater(be_pop, 0.70)
        self.assertGreater(be_pop, delta_only)

    def test_capital_allocation_and_max_loss(self):
        """DC-CSP-7: Concentration-capped contract recommendations and Max Loss."""
        cash_pool = 50000.0
        strike = 100.0
        pexec = 2.50
        max_exposure_pct = 0.25  # Max 25% of 50,000 = 12,500. Each contract = 10,000. -> 1 contract.

        alloc = calculate_csp_capital_allocation(
            cash_pool=cash_pool,
            strike=strike,
            pexec=pexec,
            max_exposure_pct=max_exposure_pct
        )

        self.assertEqual(alloc["required_capital_per_contract"], 10000.0)
        self.assertEqual(alloc["recommended_contracts"], 1)
        # Max loss per contract = (Strike - P_exec) * 100 = (100 - 2.50) * 100 = $9,750
        self.assertAlmostEqual(alloc["max_loss_per_contract"], 9750.0)
        self.assertAlmostEqual(alloc["total_max_loss"], 9750.0)
        self.assertAlmostEqual(alloc["total_premium"], 250.0)

    def test_stress_test_pnl(self):
        """DC-CSP-5: Stress test with 15% underlying single-day drop."""
        spot = 100.0
        strike = 95.0
        pexec = 2.0
        # If spot drops 15% to 85.0:
        # Put intrinsic becomes max(0, 95 - 85) = 10.0
        # PnL per share = P_exec - Intrinsic = 2.0 - 10.0 = -8.0
        # Per contract (100 shares) = -$800.0
        stress_pnl = calculate_stress_test_pnl(spot=spot, strike=strike, pexec=pexec, drop_pct=0.15)
        self.assertAlmostEqual(stress_pnl, -800.0, places=2)

    def test_dc_csp_1_nan_and_inf_interception(self):
        """DC-CSP-1 & CONCERN-2: Ensure NaN/Inf solver returns are intercepted and physically bounded."""
        import unittest.mock as mock
        with mock.patch("src.leaps_scanner.core.american_pricing.bjerksund_stensland_2002", return_value=float("nan")):
            price = bjerksund_stensland_put(spot=100.0, strike=105.0, t=30/365, r=0.05, q=0.01, sigma=0.20)
            self.assertFalse(math.isnan(price))
            self.assertGreaterEqual(price, 5.0)  # intrinsic is 5.0

    def test_dc_csp_2_dividend_window_calibration(self):
        """DC-CSP-2: 7~45 DTE dividend event window calibration and decay factor."""
        spot = 100.0
        strike = 100.0
        t = 30.0 / 365.0  # 30 DTE
        r = 0.05
        q = 0.04
        sigma = 0.20

        # Case 1: Ex-date is AFTER expiry (ex_date = 40d > 30d). Dividend should NOT discount option (eff_q = 0).
        p_ex_after = bjerksund_stensland_put(spot, strike, t, r, q, sigma, ex_date_t=40.0 / 365.0)
        p_zero_q = bjerksund_stensland_put(spot, strike, t, r, 0.0, sigma, ex_date_t=None)
        # With eff_q = 0, price should match zero-dividend put
        self.assertAlmostEqual(p_ex_after, p_zero_q, places=3)

        # Case 2: Ex-date is BEFORE expiry (ex_date = 10d <= 30d) with discrete dividend = $1.00
        # Spot drops by PV of dividend
        p_ex_before = bjerksund_stensland_put(spot, strike, t, r, q, sigma, ex_date_t=10.0 / 365.0, discrete_dividend=1.0)
        # Put price should be higher because spot drops by $1.00 before expiry
        self.assertGreater(p_ex_before, p_ex_after)

        # Case 3: Ex-date unverified on short DTE -> decay factor applied
        p_damped = bjerksund_stensland_put(spot, strike, t, r, q, sigma, ex_date_t=None, decay_unverified_dividend=True)
        self.assertGreater(p_damped, 0.0)

    def test_otm_put_greeks_delta_range(self):
        """DC-CSP-3: OTM Put must have small negative Delta (e.g. -0.15 to -0.30), NOT inverted deep-ITM Delta."""
        spot = 750.0
        strike = 725.0  # OTM put (~3.3% buffer)
        t = 30.0 / 365.25
        r = 0.04
        q = 0.01
        iv = 0.15

        g = calculate_american_put_greeks(spot, strike, t, r, q, iv)
        # OTM Put Delta should be around -0.15 to -0.25, NOT -0.80 or -1.00!
        self.assertLess(g.delta, 0.0)
        self.assertGreater(g.delta, -0.40)
        self.assertLess(g.delta, -0.10)


if __name__ == "__main__":
    unittest.main()

