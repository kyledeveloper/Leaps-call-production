"""
Board 3: Cash Secured Put - High IV Rank Harvest Strategy.
Targets elevated volatility underlyings (IV Rank >= 50%) to harvest rich extrinsic premium
accelerating Theta decay and capitalizing on volatility mean-reversion.
Characteristics:
- IV Rank / Percentile: >= 0.50 (PASS), 0.35 to 0.50 (WATCH)
- Target Delta: -0.15 to -0.40
- DTE: 7 to 45 days (DTE < 7 hard rejected per DC-CSP-4)
"""
from dataclasses import dataclass, field
from typing import List
from src.leaps_scanner.strategies.guards import GuardStatus


@dataclass(frozen=True)
class CSPVolRankResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0


def evaluate_csp_vol_rank(candidate) -> CSPVolRankResult:
    """
    Evaluate candidate for Board 3 (High IV Rank Harvest).
    """
    reasons = []

    # 1. DTE Guard (DC-CSP-4)
    if candidate.dte < 7.0:
        return CSPVolRankResult(status=GuardStatus.REJECT, reasons=["DTE_UNDER_7D_PROHIBITED"])

    # 2. Zero Bid / Inverted Market Guard (DC-CSP-6)
    if candidate.bid <= 0.0 or candidate.bid >= candidate.ask:
        return CSPVolRankResult(status=GuardStatus.REJECT, reasons=["INVALID_OR_ZERO_BID"])

    # 3. IV Rank / Percentile Gate
    ivr = candidate.iv_rank if candidate.iv_rank is not None else candidate.iv_percentile
    if ivr is None:
        ivr = 0.40  # default neutral fallback

    if ivr >= 0.50:
        ivr_status = GuardStatus.PASS
    elif ivr >= 0.35:
        ivr_status = GuardStatus.WATCH
        reasons.append(f"IVR_MODERATE_{ivr*100:.1f}%")
    else:
        return CSPVolRankResult(status=GuardStatus.REJECT, reasons=[f"IVR_LOW_{ivr*100:.1f}%"])

    # 4. Delta window check (-0.15 to -0.40) (DC-CSP-3: Strict negative delta enforcement)
    if candidate.delta >= 0.0:
        return CSPVolRankResult(status=GuardStatus.REJECT, reasons=["POSITIVE_DELTA_PROHIBITED"])
    abs_delta = abs(candidate.delta)
    if 0.15 <= abs_delta <= 0.40:
        delta_status = GuardStatus.PASS
    elif 0.10 <= abs_delta < 0.15 or 0.40 < abs_delta <= 0.45:
        delta_status = GuardStatus.WATCH
        reasons.append(f"DELTA_WATCH_{candidate.delta:.2f}")
    else:
        return CSPVolRankResult(status=GuardStatus.REJECT, reasons=[f"DELTA_OUT_OF_RANGE_{candidate.delta:.2f}"])

    final_status = GuardStatus.WATCH if (ivr_status == GuardStatus.WATCH or delta_status == GuardStatus.WATCH) else GuardStatus.PASS
    return CSPVolRankResult(status=final_status, reasons=reasons)
