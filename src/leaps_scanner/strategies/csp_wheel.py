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
from typing import Dict, List
from src.leaps_scanner.strategies.guards import GuardStatus, fold_gates, evaluate_liquidity_guard


@dataclass(frozen=True)
class CSPWheelResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0
    gates: Dict[str, GuardStatus] = field(default_factory=dict)


def evaluate_csp_wheel(candidate) -> CSPWheelResult:
    """
    Evaluate candidate for Board 2 (Wheel / Dip-Buying).
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
        if 0.30 <= abs_delta <= 0.45:
            delta_status = GuardStatus.PASS
        elif 0.25 <= abs_delta < 0.30 or 0.45 < abs_delta <= 0.50:
            delta_status = GuardStatus.WATCH
            reasons.append(f"WHEEL_DELTA_WATCH_{delta:.2f}")
        else:
            delta_status = GuardStatus.REJECT
            reasons.append(f"WHEEL_DELTA_OUT_OF_RANGE_{delta:.2f}")
    gates["delta"] = delta_status

    # 3. Oversold / Dip preference
    dip = candidate.rsi_14 <= 50.0 or candidate.pct_to_200dma <= 0.0
    if dip:
        tech_status = GuardStatus.PASS
    elif candidate.rsi_14 <= 65.0:
        tech_status = GuardStatus.WATCH
        reasons.append(
            f"NO_DIP_RSI_{candidate.rsi_14:.1f}_DMA_{candidate.pct_to_200dma:.1%}"
        )
    else:
        tech_status = GuardStatus.REJECT
        reasons.append(f"RSI_OVERBOUGHT_{candidate.rsi_14:.1f}")
    gates["oversold"] = tech_status

    # 4. Downside Buffer Gate (Wheel targets -0.30 to -0.45 delta, buffer >= 3% is PASS)
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
    return CSPWheelResult(status=final_status, reasons=reasons, gates=gates)
