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
from typing import Dict, List
from src.leaps_scanner.strategies.guards import GuardStatus, fold_gates, evaluate_liquidity_guard


@dataclass(frozen=True)
class CSPVolRankResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0
    gates: Dict[str, GuardStatus] = field(default_factory=dict)


def evaluate_csp_vol_rank(candidate) -> CSPVolRankResult:
    """
    Evaluate candidate for Board 3 (High IV Rank Harvest).
    Returns status and per-filter gates for dynamic UI re-ranking.
    """
    reasons = []
    gates: Dict[str, GuardStatus] = {}

    # 1. DTE Guard (DC-CSP-4)
    if candidate.dte < 7.0:
        dte_status = GuardStatus.REJECT
        reasons.append("DTE_UNDER_7D_PROHIBITED")
    elif candidate.dte < 14.0:
        dte_status = GuardStatus.WATCH
        reasons.append(f"DTE_SHORT_{int(candidate.dte)}D")
    else:
        dte_status = GuardStatus.PASS
    gates["dte"] = dte_status

    # 2. IV Rank / Percentile Gate
    ivr = candidate.iv_rank if candidate.iv_rank is not None else candidate.iv_percentile
    if ivr is None:
        ivr = 0.40  # default neutral fallback

    if ivr >= 0.50:
        ivr_status = GuardStatus.PASS
    elif ivr >= 0.35:
        ivr_status = GuardStatus.WATCH
        reasons.append(f"IVR_MODERATE_{ivr*100:.1f}%")
    else:
        ivr_status = GuardStatus.REJECT
        reasons.append(f"IVR_LOW_{ivr*100:.1f}%")
    gates["ivr"] = ivr_status

    # 3. Delta window check (-0.15 to -0.40) (DC-CSP-3: Strict negative delta enforcement)
    delta = candidate.delta
    if delta >= 0.0:
        delta_status = GuardStatus.REJECT
        reasons.append("POSITIVE_DELTA_PROHIBITED")
    else:
        abs_delta = abs(delta)
        if 0.15 <= abs_delta <= 0.40:
            delta_status = GuardStatus.PASS
        elif 0.10 <= abs_delta < 0.15 or 0.40 < abs_delta <= 0.45:
            delta_status = GuardStatus.WATCH
            reasons.append(f"DELTA_WATCH_{candidate.delta:.2f}")
        else:
            delta_status = GuardStatus.REJECT
            reasons.append(f"DELTA_OUT_OF_RANGE_{candidate.delta:.2f}")
    gates["delta"] = delta_status

    # 4. Earnings event risk gate
    e_status = getattr(candidate, "earnings_status", None)
    e_status_str = e_status.value if hasattr(e_status, "value") else str(e_status or "")
    if "IMPACTED" in e_status_str:
        earn_status = GuardStatus.REJECT
        reasons.append("EARNINGS_WITHIN_DTE_WINDOW")
    elif "UNVERIFIED" in e_status_str:
        earn_status = GuardStatus.WATCH
        reasons.append("EARNINGS_UNVERIFIED")
    else:
        earn_status = GuardStatus.PASS
    gates["earnings"] = earn_status

    # 5. Liquidity Guard
    if candidate.bid <= 0.0 or candidate.bid >= candidate.ask:
        liq_status = GuardStatus.REJECT
        reasons.append("INVALID_OR_ZERO_BID")
    else:
        l_guard = evaluate_liquidity_guard(
            bid=candidate.bid,
            ask=candidate.ask,
            open_interest=candidate.open_interest,
            volume=candidate.volume,
            bid_size=candidate.bid_size,
            ask_size=candidate.ask_size,
        )
        if l_guard.spread_status == GuardStatus.REJECT or l_guard.oi_status == GuardStatus.REJECT:
            liq_status = GuardStatus.REJECT
        elif l_guard.spread_status == GuardStatus.WATCH or l_guard.volume_status == GuardStatus.WATCH:
            liq_status = GuardStatus.WATCH
        else:
            liq_status = GuardStatus.PASS
        reasons.extend(l_guard.reasons)
    gates["liquidity"] = liq_status

    final_status = fold_gates(gates)
    return CSPVolRankResult(status=final_status, reasons=reasons, gates=gates)
