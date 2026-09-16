"""
Bjerksund-Stensland American Call Option Pricing Engine.
Includes Black-Scholes closed-form model for European lower bound and no-dividend equality.
Adheres to Defensive Clause 1 and Defensive Clause 4 (bounds C <= S).
"""
import math
from typing import Optional


def _cnd(x: float) -> float:
    """Cumulative Normal Distribution function Phi(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def black_scholes_call(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    sigma: float
) -> float:
    """
    Standard Black-Scholes European Call option price with continuous dividend yield q.
    """
    if spot <= 0.0:
        return 0.0
    if strike <= 0.0:
        return spot * math.exp(-q * t)
    if t <= 0.0:
        return max(0.0, spot - strike)
    if sigma <= 0.0:
        return max(0.0, spot * math.exp(-q * t) - strike * math.exp(-r * t))

    b = r - q
    d1 = (math.log(spot / strike) + (b + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)

    price = spot * math.exp(-q * t) * _cnd(d1) - strike * math.exp(-r * t) * _cnd(d2)
    return max(0.0, price)


def _bs_phi(
    s: float,
    gamma: float,
    h: float,
    i: float,
    r_t: float,
    b_t: float,
    variance: float
) -> float:
    """
    Auxiliary phi function for Bjerksund-Stensland.
    Computes discounted probability integral over early exercise boundary.
    """
    lambda_val = -r_t + gamma * b_t + 0.5 * gamma * (gamma - 1.0) * variance
    sqrt_var = math.sqrt(variance)
    d = -(math.log(s / h) + (b_t + (gamma - 0.5) * variance)) / sqrt_var
    kappa = (2.0 * b_t) / variance + (2.0 * gamma - 1.0)

    term = _cnd(d) - ((i / s) ** kappa) * _cnd(d - (2.0 * math.log(i / s)) / sqrt_var)
    return math.exp(lambda_val) * term


def bjerksund_stensland_2002(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    sigma: float
) -> float:
    """
    Bjerksund-Stensland American Call Option Model.
    Prices American calls with early exercise boundary and continuous dividend yield.
    """
    if spot <= 0.0 or strike <= 0.0:
        return 0.0
    if t <= 0.0:
        return max(0.0, spot - strike)

    eur_call = black_scholes_call(spot, strike, t, r, q, sigma)

    b = r - q
    # When cost of carry b >= r (i.e. q <= 0), early exercise is never optimal
    if b >= r or q <= 1e-7:
        return eur_call

    if sigma <= 1e-4:
        return max(eur_call, spot - strike)

    variance = sigma * sigma * t
    r_t = r * t
    b_t = b * t

    disc = (b_t / variance - 0.5) ** 2 + (2.0 * r_t) / variance
    if disc < 0:
        return eur_call
    beta = (0.5 - b_t / variance) + math.sqrt(disc)

    if beta <= 1.0:
        return eur_call

    b_infinity = (beta / (beta - 1.0)) * strike
    b_0 = strike if b_t == r_t else max(strike, (r_t / (r_t - b_t)) * strike)

    ht = -(b_t + 2.0 * math.sqrt(variance)) * (b_0 / (b_infinity - b_0))
    i_trigger = b_0 + (b_infinity - b_0) * (1.0 - math.exp(ht))

    # Immediate exercise check
    if spot >= i_trigger:
        return spot - strike

    fwd = spot * math.exp(b_t)
    q_val = math.log(i_trigger / fwd) / math.sqrt(variance)
    if q_val > 12.5:
        return eur_call

    # Valuation decomposition
    call_val = (
        (i_trigger - strike) * ((spot / i_trigger) ** beta) * (1.0 - _bs_phi(spot, beta, i_trigger, i_trigger, r_t, b_t, variance))
        + spot * _bs_phi(spot, 1.0, i_trigger, i_trigger, r_t, b_t, variance)
        - spot * _bs_phi(spot, 1.0, strike, i_trigger, r_t, b_t, variance)
        - strike * _bs_phi(spot, 0.0, i_trigger, i_trigger, r_t, b_t, variance)
        + strike * _bs_phi(spot, 0.0, strike, i_trigger, r_t, b_t, variance)
    )

    # Enforce strict physical bounds (Defensive Clauses 1 & 4)
    # 1. Lower bound: American Call >= European Call >= Intrinsic
    call_val = max(call_val, eur_call, spot - strike, 0.0)
    # 2. Upper bound: American Call <= Spot
    call_val = min(call_val, spot)

    return call_val
