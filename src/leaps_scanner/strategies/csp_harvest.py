"""
Board 1: Cash Secured Put - Premium Harvesting Strategy.
Targets OTM puts for pure income generation.
Characteristics:
- Target Delta: -0.15 to -0.30 (PASS), -0.10 to -0.35 (WATCH)
- DTE: 7 to 45 days (DTE < 7 hard rejected per DC-CSP-4)
- Downside Buffer: >= 3.0% (PASS), 1.0% to 3.0% (WATCH)
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.leaps_scanner.strategies.guards import GuardStatus, GuardResult, fold_gates, evaluate_liquidity_guard


@dataclass(frozen=True)
class CSPHarvestResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0
    gates: Dict[str, GuardStatus] = field(default_factory=dict)


def evaluate_csp_harvest(candidate) -> CSPHarvestResult:
    """
    Evaluate candidate for Board 1 (Premium Harvesting).
    Returns status and per-filter gates for dynamic UI re-ranking.
    """
    reasons = []
    gates: Dict[str, GuardStatus] = {}

    # 1. DTE Gate: Neutral PASS (DC-CSP-10: Filtering delegated to user-selected buckets)
    gates["dte"] = GuardStatus.PASS

    # 2. Delta Gate (DC-CSP-3: Strict negative delta enforcement)
    delta = candidate.delta
    if delta >= 0.0:
        delta_status = GuardStatus.REJECT
        reasons.append("POSITIVE_DELTA_PROHIBITED")
    else:
        abs_delta = abs(delta)
        if 0.15 <= abs_delta <= 0.30:
            delta_status = GuardStatus.PASS
        elif 0.10 <= abs_delta < 0.15 or 0.30 < abs_delta <= 0.35:
            delta_status = GuardStatus.WATCH
            reasons.append(f"DELTA_WATCH_{delta:.2f}")
        else:
            delta_status = GuardStatus.REJECT
            reasons.append(f"DELTA_OUT_OF_RANGE_{delta:.2f}")
    gates["delta"] = delta_status

    # 3. Downside Buffer Gate
    buffer = (candidate.spot - candidate.strike) / candidate.spot if candidate.spot > 0 else 0.0
    if buffer >= 0.03:
        buffer_status = GuardStatus.PASS
    elif buffer >= 0.01:
        buffer_status = GuardStatus.WATCH
        reasons.append(f"BUFFER_THIN_{buffer*100:.1f}%")
    else:
        buffer_status = GuardStatus.REJECT
        reasons.append(f"BUFFER_TOO_NARROW_{buffer*100:.1f}%")
    gates["buffer"] = buffer_status

    # 4. Liquidity Guard
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
    return CSPHarvestResult(status=final_status, reasons=reasons, gates=gates)
