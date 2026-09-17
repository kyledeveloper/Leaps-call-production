"""
Hybrid Implied Volatility (IV) solver.
Combines Arbitrage Boundary Pre-Checks, Newton-Raphson, and Brent's Method.
Adheres strictly to Defensive Clause 4.
"""
import math
from dataclasses import dataclass
from typing import Optional
from src.leaps_scanner.core.american_pricing import (
    bjerksund_stensland_2002,
    bjerksund_stensland_put,
)


@dataclass(frozen=True)
class IVResult:
    iv: Optional[float]
    status: str  # "CONVERGED", "ARBITRAGE_VIOLATION", "ZERO_EXTRINSIC", "IV_UNAVAILABLE"


def _brent_root(f, a: float, b: float, tol: float = 1e-5, max_iter: int = 50) -> Optional[float]:
    """
    Van Wijngaarden-Dekker-Brent root-finding algorithm.
    Pure Python, zero external dependency.
    """
    fa = f(a)
    fb = f(b)

    if fa * fb > 0.0:
        return None  # Root not bracketed

    if abs(fa) < abs(fb):
        a, b = b, a
        fa, fb = fb, fa

    c = a
    fc = fa
    mflag = True
    d = 0.0

    for _ in range(max_iter):
        if abs(fb) < tol:
            return b

        if abs(b - a) < tol:
            return b

        if fa != fc and fb != fc:
            # Inverse quadratic interpolation
            s = (
                a * fb * fc / ((fa - fb) * (fa - fc))
                + b * fa * fc / ((fb - fa) * (fb - fc))
                + c * fa * fb / ((fc - fa) * (fc - fb))
            )
        else:
            # Secant method
            s = b - fb * (b - a) / (fb - fa)

        # Conditions to accept interpolation or bisect
        cond1 = not (((3.0 * a + b) / 4.0 <= s <= b) if a < b else (b <= s <= (3.0 * a + b) / 4.0))
        cond2 = mflag and (abs(s - b) >= abs(b - c) / 2.0)
        cond3 = (not mflag) and (abs(s - b) >= abs(c - d) / 2.0)
        cond4 = mflag and (abs(b - c) < tol)
        cond5 = (not mflag) and (abs(c - d) < tol)

        if cond1 or cond2 or cond3 or cond4 or cond5:
            # Bisection method
            s = (a + b) / 2.0
            mflag = True
        else:
            mflag = False

        fs = f(s)
        d = c
        c = b
        fc = fb

        if fa * fs < 0.0:
            b = s
            fb = fs
        else:
            a = s
            fa = fs

        if abs(fa) < abs(fb):
            a, b = b, a
            fa, fb = fb, fa

    return b


def solve_implied_volatility(
    price: float,
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    initial_guess: float = 0.30
) -> IVResult:
    """
    Solve for American Call Implied Volatility.
    Pre-checks arbitrage bounds and seamlessly falls back from Newton to Brent.
    """
    if spot <= 0.0 or strike <= 0.0 or t <= 0.0:
        return IVResult(iv=None, status="INVALID_INPUTS")

    # 1. Arbitrage Boundary Pre-Check (Defensive Clause 4)
    # Lower bound: max(0, S - K, S*e^(-qT) - K*e^(-rT))
    eur_lower = max(0.0, spot * math.exp(-q * t) - strike * math.exp(-r * t))
    intrinsic = max(0.0, spot - strike)
    lower_bound = max(eur_lower, intrinsic)

    # Upper bound: American Call <= Spot
    upper_bound = spot

    if price < lower_bound - 1e-4 or price > upper_bound + 1e-4:
        return IVResult(iv=None, status="ARBITRAGE_VIOLATION")

    # Pure intrinsic (zero extrinsic) check
    if abs(price - intrinsic) < 1e-4:
        return IVResult(iv=None, status="ZERO_EXTRINSIC")

    def model_price(vol: float) -> float:
        return bjerksund_stensland_2002(spot, strike, t, r, q, max(1e-4, vol))

    def price_diff(vol: float) -> float:
        return model_price(vol) - price

    # 2. Newton-Raphson Fast Solver
    vol = max(0.05, min(1.5, initial_guess))
    converged = False

    for _ in range(25):
        curr_price = model_price(vol)
        diff = curr_price - price

        if abs(diff) < 1e-4:
            converged = True
            break

        # Numerical Vega
        d_vol = 0.002
        p_up = model_price(vol + d_vol)
        p_down = model_price(max(1e-4, vol - d_vol))
        vega = (p_up - p_down) / (2.0 * d_vol)

        if vega < 1e-5:
            # Low Vega: Newton step unreliable, fall back to Brent
            break

        step = diff / vega
        vol -= step

        if vol <= 0.001 or vol > 4.0:
            break

    if converged and 0.001 < vol < 5.0:
        return IVResult(iv=round(vol, 4), status="CONVERGED")

    # 3. Brent's Method Guaranteed Fallback
    brent_vol = _brent_root(price_diff, a=0.001, b=5.0, tol=1e-4, max_iter=40)
    if brent_vol is not None and 0.001 <= brent_vol <= 5.0:
        return IVResult(iv=round(brent_vol, 4), status="CONVERGED")

    return IVResult(iv=None, status="IV_UNAVAILABLE")


def solve_american_put_iv(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    price: float,
    initial_guess: float = 0.30
) -> IVResult:
    """
    Solve for American Put Implied Volatility.
    Pre-checks arbitrage bounds and seamlessly falls back from Newton to Brent.
    Adheres to DC-CSP-1.
    """
    if spot <= 0.0 or strike <= 0.0 or t <= 0.0:
        return IVResult(iv=None, status="INVALID_INPUTS")

    # Lower bound: max(0, K - S, K*e^(-rT) - S*e^(-qT))
    eur_lower = max(0.0, strike * math.exp(-r * t) - spot * math.exp(-q * t))
    intrinsic = max(0.0, strike - spot)
    lower_bound = max(eur_lower, intrinsic)

    # Upper bound: American Put <= Strike
    upper_bound = strike

    if price < lower_bound - 1e-4 or price > upper_bound + 1e-4:
        return IVResult(iv=None, status="ARBITRAGE_VIOLATION")

    if abs(price - intrinsic) < 1e-4:
        return IVResult(iv=None, status="ZERO_EXTRINSIC")

    def model_price(vol: float) -> float:
        return bjerksund_stensland_put(spot, strike, t, r, q, max(1e-4, vol))

    def price_diff(vol: float) -> float:
        return model_price(vol) - price

    # Newton-Raphson
    vol = max(0.05, min(1.5, initial_guess))
    converged = False

    for _ in range(25):
        curr_price = model_price(vol)
        diff = curr_price - price

        if abs(diff) < 1e-4:
            converged = True
            break

        d_vol = 0.002
        p_up = model_price(vol + d_vol)
        p_down = model_price(max(1e-4, vol - d_vol))
        vega = (p_up - p_down) / (2.0 * d_vol)

        if vega < 1e-5:
            break

        step = diff / vega
        vol -= step

        if vol <= 0.001 or vol > 4.0:
            break

    if converged and 0.001 < vol < 5.0:
        return IVResult(iv=round(vol, 4), status="CONVERGED")

    # Brent fallback
    brent_vol = _brent_root(price_diff, a=0.001, b=5.0, tol=1e-4, max_iter=40)
    if brent_vol is not None and 0.001 <= brent_vol <= 5.0:
        return IVResult(iv=round(brent_vol, 4), status="CONVERGED")

    return IVResult(iv=None, status="IV_UNAVAILABLE")

