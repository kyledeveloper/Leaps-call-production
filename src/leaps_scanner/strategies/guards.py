"""
Shared liquidity and quote guardrail module.
Evaluates contracts against Pass, Watch, and Reject criteria prior to strategy ranking.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class GuardStatus(str, Enum):
    PASS = "PASS"
    WATCH = "WATCH"
    REJECT = "REJECT"


def fold_gates(gates: Dict[str, GuardStatus]) -> GuardStatus:
    vals = list(gates.values())
    if GuardStatus.REJECT in vals:
        return GuardStatus.REJECT
    if GuardStatus.WATCH in vals:
        return GuardStatus.WATCH
    return GuardStatus.PASS


def signal_tier(core: bool, weak: bool) -> GuardStatus:
    if core:
        return GuardStatus.PASS
    if weak:
        return GuardStatus.WATCH
    return GuardStatus.REJECT


@dataclass(frozen=True)
class GuardResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    spread_status: GuardStatus = GuardStatus.PASS
    oi_status: GuardStatus = GuardStatus.PASS
    volume_status: GuardStatus = GuardStatus.PASS

    @property
    def is_rejected(self) -> bool:
        return self.status == GuardStatus.REJECT

    @property
    def is_pass(self) -> bool:
        return self.status == GuardStatus.PASS

    @property
    def is_watch(self) -> bool:
        return self.status == GuardStatus.WATCH


def evaluate_liquidity_guard(
    bid: float,
    ask: float,
    open_interest: int,
    volume: int = 0,
    avg_volume_20d: float = 0.0,
    bid_size: int = 0,
    ask_size: int = 0,
    quote_age_seconds: float = 0.0,
    multiplier: int = 100,
    is_adjusted: bool = False,
    is_rth: bool = True
) -> GuardResult:
    """
    Evaluate quote and liquidity against shared guardrails.
    Spread / OI / volume are scored independently so Strategy 1 can toggle them.
    """
    reasons: List[str] = []

    if open_interest >= 300:
        oi_status = GuardStatus.PASS
    elif open_interest >= 100:
        oi_status = GuardStatus.WATCH
    else:
        oi_status = GuardStatus.REJECT
        reasons.append(f"LOW_OI_{open_interest}")

    has_daily_vol = volume >= 50
    has_avg_vol = avg_volume_20d >= 20
    has_depth = (bid_size >= 10 or ask_size >= 10)
    if has_daily_vol or has_avg_vol or has_depth:
        volume_status = GuardStatus.PASS
    elif volume >= 10 and open_interest >= 100:
        volume_status = GuardStatus.WATCH
    else:
        volume_status = GuardStatus.REJECT
        reasons.append(f"INSUFFICIENT_ACTIVITY_vol={volume}")

    def _done(spread_status: GuardStatus, extra: Optional[List[str]] = None) -> GuardResult:
        all_reasons = list(reasons)
        if extra:
            all_reasons.extend(extra)
        components = {
            "spread": spread_status,
            "oi": oi_status,
            "volume": volume_status,
        }
        return GuardResult(
            status=fold_gates(components),
            reasons=all_reasons,
            spread_status=spread_status,
            oi_status=oi_status,
            volume_status=volume_status,
        )

    if multiplier != 100:
        return _done(GuardStatus.REJECT, ["NON_STANDARD"])
    if is_adjusted:
        return _done(GuardStatus.REJECT, ["ADJUSTED_CONTRACT"])
    if bid < 0:
        return _done(GuardStatus.REJECT, ["NEGATIVE_BID"])
    if ask <= 0:
        return _done(GuardStatus.REJECT, ["INVALID_ASK"])
    if bid > ask:
        return _done(GuardStatus.REJECT, ["CROSSED_MARKET"])

    mid = (bid + ask) / 2.0
    spread = ask - bid
    half_spread = spread / 2.0

    if bid <= 0.0:
        if not is_rth:
            if mid > 0 and (spread / mid) > 0.50 and open_interest < 100:
                return _done(GuardStatus.REJECT, ["ZERO_BID_LOW_LIQUIDITY"])
            return _done(GuardStatus.REJECT, ["ZERO_BID_POST_MARKET"])
        return _done(GuardStatus.REJECT, ["ZERO_BID_NO_BUYER"])

    rel_spread = (spread / mid) if mid > 0 else 1.0
    if rel_spread <= 0.06:
        rel_tier = GuardStatus.PASS
    elif rel_spread <= 0.10:
        rel_tier = GuardStatus.WATCH
    else:
        rel_tier = GuardStatus.REJECT

    abs_pass = max(0.75, 0.025 * mid)
    abs_watch = max(1.50, 0.05 * mid)
    if half_spread <= abs_pass:
        abs_tier = GuardStatus.PASS
    elif half_spread <= abs_watch:
        abs_tier = GuardStatus.WATCH
    else:
        abs_tier = GuardStatus.REJECT

    extra: List[str] = []
    if rel_tier == GuardStatus.PASS or abs_tier == GuardStatus.PASS:
        spread_status = GuardStatus.PASS
    elif rel_tier == GuardStatus.WATCH or abs_tier == GuardStatus.WATCH:
        spread_status = GuardStatus.WATCH
    else:
        spread_status = GuardStatus.REJECT
        extra.append(f"SPREAD_TOO_WIDE_rel={rel_spread:.1%}_abs=${half_spread:.2f}")
    return _done(spread_status, extra)
