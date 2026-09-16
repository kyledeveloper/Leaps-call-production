"""
Strategy 4: Unusual Far-Dated Options Flow.
Identifies anomalous large-scale accumulation in long-dated call contracts.
Requires dual confirmation of both Vol/OI multiple and Dollar Volume.
Buyer aggressor tag is disabled by default to prevent false attribution from midpoint approximations.
"""
from dataclasses import dataclass, field
from typing import List, Optional
from src.leaps_scanner.core.metrics import CONTRACT_MULTIPLIER
from src.leaps_scanner.strategies.guards import GuardStatus
from src.leaps_scanner.data.universe import SymbologyNormalizer


CAVEAT_NOTICE = (
    "Far-dated flow includes rollovers, tax-loss harvesting, and structured hedging; "
    "signals direction with lower certainty than short-dated UOA."
)


@dataclass(frozen=True)
class UnusualFlowInput:
    symbol: str
    underlying: str
    spot: float
    strike: float
    dte: float
    bid: float
    ask: float
    volume: int
    open_interest: int
    is_etf: bool = False
    liquidity_status: GuardStatus = GuardStatus.PASS
    avg_20d_volume: Optional[float] = None
    buyer_aggressor_tag: bool = False


@dataclass(frozen=True)
class UnusualFlowResult:
    symbol: str
    status: GuardStatus
    vol_oi_ratio: float
    dollar_volume: float
    volume: int
    open_interest: int
    buyer_aggressor_tag: bool
    caveat_notice: str
    reasons: List[str] = field(default_factory=list)


def evaluate_unusual_flow(inp: UnusualFlowInput) -> UnusualFlowResult:
    """
    Evaluate contract for unusual far-dated flow activity.
    """
    canonical_symbol = SymbologyNormalizer.to_canonical(inp.symbol)
    reasons: List[str] = []

    # 1. Shared liquidity check
    if inp.liquidity_status == GuardStatus.REJECT:
        reasons.append("LIQUIDITY_REJECTED")
        return UnusualFlowResult(
            symbol=canonical_symbol,
            status=GuardStatus.REJECT,
            vol_oi_ratio=0.0,
            dollar_volume=0.0,
            volume=inp.volume,
            open_interest=inp.open_interest,
            buyer_aggressor_tag=False,
            caveat_notice=CAVEAT_NOTICE,
            reasons=reasons
        )

    # 2. DTE check (>= 250)
    if inp.dte < 250.0:
        reasons.append(f"INSUFFICIENT_DTE_{inp.dte}")
        return UnusualFlowResult(
            symbol=canonical_symbol,
            status=GuardStatus.REJECT,
            vol_oi_ratio=0.0,
            dollar_volume=0.0,
            volume=inp.volume,
            open_interest=inp.open_interest,
            buyer_aggressor_tag=False,
            caveat_notice=CAVEAT_NOTICE,
            reasons=reasons
        )

    # 3. Strike window check: [0.70S, 1.35S]
    if inp.spot > 0:
        strike_ratio = inp.strike / inp.spot
        if strike_ratio < 0.70 or strike_ratio > 1.35:
            reasons.append(f"STRIKE_OUT_OF_WINDOW_{strike_ratio:.2f}")
            return UnusualFlowResult(
                symbol=canonical_symbol,
                status=GuardStatus.REJECT,
                vol_oi_ratio=0.0,
                dollar_volume=0.0,
                volume=inp.volume,
                open_interest=inp.open_interest,
                buyer_aggressor_tag=False,
                caveat_notice=CAVEAT_NOTICE,
                reasons=reasons
            )

    mid = (inp.bid + inp.ask) / 2.0
    dollar_volume = mid * CONTRACT_MULTIPLIER * inp.volume

    if inp.open_interest > 0:
        vol_oi_ratio = inp.volume / float(inp.open_interest)
    else:
        vol_oi_ratio = float(inp.volume)

    # Tier 1: Vol/OI Ratio
    # Pass: >= 3.0 and OI >= 50
    # Watch: >= 2.0
    # Reject: < 2.0
    if vol_oi_ratio >= 3.0 and inp.open_interest >= 50:
        ratio_tier = GuardStatus.PASS
    elif vol_oi_ratio >= 2.0:
        ratio_tier = GuardStatus.WATCH
    else:
        ratio_tier = GuardStatus.REJECT
        reasons.append(f"LOW_VOL_OI_RATIO_{vol_oi_ratio:.2f}")

    # Tier 2: Dollar Volume V_$
    # Single stock: Pass >= 250k, Watch >= 100k
    # ETF: Pass >= 1M, Watch >= 250k
    if inp.is_etf:
        if dollar_volume >= 1_000_000.0:
            dollar_tier = GuardStatus.PASS
        elif dollar_volume >= 250_000.0:
            dollar_tier = GuardStatus.WATCH
        else:
            dollar_tier = GuardStatus.REJECT
            reasons.append(f"LOW_DOLLAR_VOLUME_${dollar_volume:,.0f}")
    else:
        if dollar_volume >= 250_000.0:
            dollar_tier = GuardStatus.PASS
        elif dollar_volume >= 100_000.0:
            dollar_tier = GuardStatus.WATCH
        else:
            dollar_tier = GuardStatus.REJECT
            reasons.append(f"LOW_DOLLAR_VOLUME_${dollar_volume:,.0f}")

    # Tier 3: Contract count volume
    # Single stock: Pass >= 500, Watch >= 200
    # ETF: Pass >= 2000, Watch >= 500
    if inp.is_etf:
        if inp.volume >= 2000:
            count_tier = GuardStatus.PASS
        elif inp.volume >= 500:
            count_tier = GuardStatus.WATCH
        else:
            count_tier = GuardStatus.REJECT
            reasons.append(f"LOW_VOLUME_COUNT_{inp.volume}")
    else:
        if inp.volume >= 500:
            count_tier = GuardStatus.PASS
        elif inp.volume >= 200:
            count_tier = GuardStatus.WATCH
        else:
            count_tier = GuardStatus.REJECT
            reasons.append(f"LOW_VOLUME_COUNT_{inp.volume}")

    # Aggregated Status: Requires BOTH ratio AND dollar volume AND count tiers
    tiers = [ratio_tier, dollar_tier, count_tier]
    if inp.liquidity_status == GuardStatus.WATCH:
        tiers.append(GuardStatus.WATCH)

    if GuardStatus.REJECT in tiers:
        final_status = GuardStatus.REJECT
    elif GuardStatus.WATCH in tiers:
        final_status = GuardStatus.WATCH
    else:
        final_status = GuardStatus.PASS

    return UnusualFlowResult(
        symbol=canonical_symbol,
        status=final_status,
        vol_oi_ratio=vol_oi_ratio,
        dollar_volume=dollar_volume,
        volume=inp.volume,
        open_interest=inp.open_interest,
        buyer_aggressor_tag=inp.buyer_aggressor_tag,
        caveat_notice=CAVEAT_NOTICE,
        reasons=reasons
    )
