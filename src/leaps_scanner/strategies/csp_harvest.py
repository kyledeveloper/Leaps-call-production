"""
Board 1: Cash Secured Put - Premium Harvesting Strategy.
Targets OTM puts for pure income generation.
Characteristics:
- Target Delta: -0.15 to -0.30 (PASS), -0.10 to -0.35 (WATCH)
- DTE: 7 to 45 days (DTE < 7 hard rejected per DC-CSP-4)
- Downside Buffer: >= 3.0% (PASS), 1.0% to 3.0% (WATCH)
"""
from dataclasses import dataclass, field
from typing import List, Optional
from src.leaps_scanner.strategies.guards import GuardStatus, GuardResult


@dataclass(frozen=True)
class CSPHarvestResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0


def evaluate_csp_harvest(candidate) -> CSPHarvestResult:
    """
    Evaluate candidate for Board 1 (Premium Harvesting).
    """
    reasons = []

    # 1. DTE Guard (DC-CSP-4)
    if candidate.dte < 7.0:
        return CSPHarvestResult(status=GuardStatus.REJECT, reasons=["DTE_UNDER_7D_PROHIBITED"])

    # 2. Zero Bid / Inverted Market Guard (DC-CSP-6)
    if candidate.bid <= 0.0 or candidate.bid >= candidate.ask:
        return CSPHarvestResult(status=GuardStatus.REJECT, reasons=["INVALID_OR_ZERO_BID"])

    # 3. Delta Gate (DC-CSP-3: Strict negative delta enforcement)
    delta = candidate.delta
    if delta >= 0.0:
        return CSPHarvestResult(status=GuardStatus.REJECT, reasons=["POSITIVE_DELTA_PROHIBITED"])
    abs_delta = abs(delta)

    if 0.15 <= abs_delta <= 0.30:
        delta_status = GuardStatus.PASS
    elif 0.10 <= abs_delta < 0.15 or 0.30 < abs_delta <= 0.35:
        delta_status = GuardStatus.WATCH
        reasons.append(f"DELTA_WATCH_{delta:.2f}")
    else:
        return CSPHarvestResult(status=GuardStatus.REJECT, reasons=[f"DELTA_OUT_OF_RANGE_{delta:.2f}"])

    # 4. Downside Buffer Gate
    buffer = (candidate.spot - candidate.strike) / candidate.spot if candidate.spot > 0 else 0.0
    if buffer >= 0.03:
        buffer_status = GuardStatus.PASS
    elif buffer >= 0.01:
        buffer_status = GuardStatus.WATCH
        reasons.append(f"BUFFER_THIN_{buffer*100:.1f}%")
    else:
        return CSPHarvestResult(status=GuardStatus.REJECT, reasons=[f"BUFFER_TOO_NARROW_{buffer*100:.1f}%"])

    # Aggregate status
    final_status = GuardStatus.WATCH if (delta_status == GuardStatus.WATCH or buffer_status == GuardStatus.WATCH) else GuardStatus.PASS
    return CSPHarvestResult(status=final_status, reasons=reasons)
