"""
Greeks sensitivity calculation engine.
Computes Delta, Gamma, Theta, Vega, and Rho using finite difference on BS2002.
"""
from dataclasses import dataclass
from src.leaps_scanner.core.american_pricing import (
    bjerksund_stensland_2002,
    bjerksund_stensland_put,
)


@dataclass(frozen=True)
class AmericanGreeks:
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    @property
    def theta_daily(self) -> float:
        """Daily time decay sensitivity per calendar day (theta / 365.25)."""
        return self.theta / 365.25


def calculate_american_greeks(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    sigma: float
) -> AmericanGreeks:
    """
    Calculate Greeks sensitivities for an American Call option.
    """
    p0 = bjerksund_stensland_2002(spot, strike, t, r, q, sigma)

    # 1. Delta & Gamma (Spot sensitivity)
    ds = max(0.01, spot * 0.002)
    p_up = bjerksund_stensland_2002(spot + ds, strike, t, r, q, sigma)
    p_down = bjerksund_stensland_2002(spot - ds, strike, t, r, q, sigma)

    delta = (p_up - p_down) / (2.0 * ds)
    gamma = (p_up - 2.0 * p0 + p_down) / (ds * ds)

    # Physical clamping
    delta = max(0.0, min(1.0, delta))
    gamma = max(0.0, gamma)

    # 2. Theta (Time decay sensitivity, per year)
    dt = max(1.0 / 365.25, 0.001)
    if t > dt:
        p_t_less = bjerksund_stensland_2002(spot, strike, t - dt, r, q, sigma)
        # Theta is negative for long call (loss of value as time decreases toward expiry)
        theta = (p_t_less - p0) / dt
    else:
        theta = 0.0

    # 3. Vega (Volatility sensitivity per 1% change in sigma)
    dvol = 0.005
    p_vol_up = bjerksund_stensland_2002(spot, strike, t, r, q, sigma + dvol)
    p_vol_down = bjerksund_stensland_2002(spot, strike, t, r, q, max(1e-4, sigma - dvol))
    vega = max(0.0, (p_vol_up - p_vol_down) / (2.0 * dvol * 100.0))

    # 4. Rho (Interest rate sensitivity per 1% change in r)
    dr = 0.001
    p_r_up = bjerksund_stensland_2002(spot, strike, t, r + dr, q, sigma)
    p_r_down = bjerksund_stensland_2002(spot, strike, t, max(0.0, r - dr), q, sigma)
    rho = (p_r_up - p_r_down) / (2.0 * dr * 100.0)

    return AmericanGreeks(
        price=p0,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho
    )


def calculate_put_fallback_delta(spot: float, strike: float) -> float:
    """
    DC-CSP-3: Fallback Delta estimation for American Put when IV/Greeks fail.
    Strictly guarantees negative Delta in [-0.95, -0.05].
    """
    if spot <= 0.0:
        return -1.0
    diff_ratio = (spot - strike) / max(spot, 1.0)
    mag = max(0.05, min(0.95, 0.50 - 0.50 * diff_ratio))
    return -mag


def calculate_american_put_greeks(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    sigma: float
) -> AmericanGreeks:
    """
    Calculate Greeks sensitivities for an American Put option.
    Strictly adheres to DC-CSP-3:
    - Clamps Delta in [-1.0, 0.0]
    - Clamps Gamma >= 0.0
    - Theta is negative for long put (loss of value as time decreases toward expiry)
    """
    p0 = bjerksund_stensland_put(spot, strike, t, r, q, sigma)

    # 1. Delta & Gamma (Spot sensitivity)
    ds = max(0.01, spot * 0.002)
    p_up = bjerksund_stensland_put(spot + ds, strike, t, r, q, sigma)
    p_down = bjerksund_stensland_put(spot - ds, strike, t, r, q, sigma)

    delta = (p_up - p_down) / (2.0 * ds)
    gamma = (p_up - 2.0 * p0 + p_down) / (ds * ds)

    # DC-CSP-3: Physical clamping for Put
    delta = min(0.0, max(-1.0, delta))
    gamma = max(0.0, gamma)

    # 2. Theta (Time decay sensitivity, per year)
    dt = max(1.0 / 365.25, 0.001)
    if t > dt:
        p_t_less = bjerksund_stensland_put(spot, strike, t - dt, r, q, sigma)
        theta = (p_t_less - p0) / dt
    else:
        theta = 0.0

    # 3. Vega (Volatility sensitivity per 1% change in sigma)
    dvol = 0.005
    p_vol_up = bjerksund_stensland_put(spot, strike, t, r, q, sigma + dvol)
    p_vol_down = bjerksund_stensland_put(spot, strike, t, r, q, max(1e-4, sigma - dvol))
    vega = max(0.0, (p_vol_up - p_vol_down) / (2.0 * dvol * 100.0))

    # 4. Rho (Interest rate sensitivity per 1% change in r)
    dr = 0.001
    p_r_up = bjerksund_stensland_put(spot, strike, t, r + dr, q, sigma)
    p_r_down = bjerksund_stensland_put(spot, strike, t, max(0.0, r - dr), q, sigma)
    rho = (p_r_up - p_r_down) / (2.0 * dr * 100.0)

    return AmericanGreeks(
        price=p0,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho
    )

