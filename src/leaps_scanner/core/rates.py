"""
Interest rate curve and term structure interpolation module.
Interpolates risk-free rates across maturities with offline fallback table.
Adheres to Defensive Clause 1.
"""
from typing import Dict, Optional

# Default fallback term structure (Treasury / SOFR baseline)
DEFAULT_RATE_CURVE: Dict[float, float] = {
    0.25: 0.0460,   # 3M
    0.50: 0.0445,   # 6M
    1.00: 0.0420,   # 1Y
    2.00: 0.0400,   # 2Y
    3.00: 0.0390,   # 3Y
    5.00: 0.0385    # 5Y
}


class RateCurve:
    def __init__(self, curve: Optional[Dict[float, float]] = None, asof: str = "2026-09-15"):
        self.curve = curve or DEFAULT_RATE_CURVE
        self.asof = asof
        self._sorted_tenors = sorted(self.curve.keys())

    def get_rate(self, t_years: float) -> float:
        """
        Linearly interpolate rate for maturity T in years.
        Clamps to edge rates if T is outside tenor bounds.
        """
        if t_years <= 0:
            return self.curve[self._sorted_tenors[0]]

        if t_years <= self._sorted_tenors[0]:
            return self.curve[self._sorted_tenors[0]]

        if t_years >= self._sorted_tenors[-1]:
            return self.curve[self._sorted_tenors[-1]]

        # Linear interpolation between adjacent tenors
        for i in range(len(self._sorted_tenors) - 1):
            t1 = self._sorted_tenors[i]
            t2 = self._sorted_tenors[i + 1]
            if t1 <= t_years <= t2:
                r1 = self.curve[t1]
                r2 = self.curve[t2]
                weight = (t_years - t1) / (t2 - t1)
                return r1 + weight * (r2 - r1)

        return self.curve[self._sorted_tenors[-1]]
