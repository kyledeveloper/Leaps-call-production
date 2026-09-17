"""
Board 2: Cash Secured Put - Wheel / Dip-Buying Strategy.
Targets moderate Delta puts on fundamentally sound or technically oversold underlyings
at attractive valuation/strike with substantial downside buffer.
Characteristics:
- Target Delta: -0.30 to -0.45 (PASS), -0.25 to -0.50 (WATCH)
- DTE: 7 to 45 days (DTE < 7 hard rejected per DC-CSP-4)
- Value / Dip preference: RSI <= 50 or pullback near 200 DMA
"""
from dataclasses import dataclass, field
from typing import List
from src.leaps_scanner.strategies.guards import GuardStatus


@dataclass(frozen=True)
class CSPWheelResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0


def evaluate_csp_wheel(candidate) -> CSPWheelResult:
    """
    Evaluate candidate for Board 2 (Wheel / Dip-Buying).
    """
    reasons = []

    # 1. DTE Guard (DC-CSP-4)
    if candidate.dte < 7.0:
        return CSPWheelResult(status=GuardStatus.REJECT, reasons=["DTE_UNDER_7D_PROHIBITED"])

    # 2. Zero Bid / Inverted Market Guard (DC-CSP-6)
    if candidate.bid <= 0.0 or candidate.bid >= candidate.ask:
        return CSPWheelResult(status=GuardStatus.REJECT, reasons=["INVALID_OR_ZERO_BID"])

    # 3. Delta Gate (DC-CSP-3: Strict negative delta enforcement)
    delta = candidate.delta
    if delta >= 0.0:
        return CSPWheelResult(status=GuardStatus.REJECT, reasons=["POSITIVE_DELTA_PROHIBITED"])
    abs_delta = abs(delta)

    if 0.30 <= abs_delta <= 0.45:
        delta_status = GuardStatus.PASS
    elif 0.25 <= abs_delta < 0.30 or 0.45 < abs_delta <= 0.50:
        delta_status = GuardStatus.WATCH
        reasons.append(f"WHEEL_DELTA_WATCH_{delta:.2f}")
    else:
        return CSPWheelResult(status=GuardStatus.REJECT, reasons=[f"WHEEL_DELTA_OUT_OF_RANGE_{delta:.2f}"])

    # 4. Dip / valuation: PASS needs RSI<=50 or trade at/below 200DMA.
    dip = candidate.rsi_14 <= 50.0 or candidate.pct_to_200dma <= 0.0
    tech_watch = False
    if not dip:
        tech_watch = True
        reasons.append(
            f"NO_DIP_RSI_{candidate.rsi_14:.1f}_DMA_{candidate.pct_to_200dma:.1%}"
        )
    if candidate.rsi_14 > 65.0:
        tech_watch = True
        reasons.append(f"RSI_OVERBOUGHT_{candidate.rsi_14:.1f}")

    final_status = GuardStatus.WATCH if (delta_status == GuardStatus.WATCH or tech_watch) else GuardStatus.PASS
    return CSPWheelResult(status=final_status, reasons=reasons)
