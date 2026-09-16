"""
Shared liquidity and quote guardrail module.
Evaluates contracts against Pass, Watch, and Reject criteria prior to strategy ranking.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class GuardStatus(str, Enum):
    PASS = "PASS"
    WATCH = "WATCH"
    REJECT = "REJECT"


@dataclass(frozen=True)
class GuardResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)

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
    Returns Pass, Watch, or Reject with structured diagnostics.
    """
    reasons: List[str] = []

    # 1. Basic Structural & Sanity Checks
    if multiplier != 100:
        return GuardResult(status=GuardStatus.REJECT, reasons=["NON_STANDARD"])

    if is_adjusted:
        return GuardResult(status=GuardStatus.REJECT, reasons=["ADJUSTED_CONTRACT"])

    if bid < 0:
        return GuardResult(status=GuardStatus.REJECT, reasons=["NEGATIVE_BID"])

    if ask <= 0:
        return GuardResult(status=GuardStatus.REJECT, reasons=["INVALID_ASK"])

    if bid > ask:
        return GuardResult(status=GuardStatus.REJECT, reasons=["CROSSED_MARKET"])

    mid = (bid + ask) / 2.0
    spread = ask - bid
    half_spread = spread / 2.0

    # 2. Zero-Bid & Trash Quote Hygiene (Reject all zero-bid contracts)
    if bid <= 0.0:
        if not is_rth:
            if mid > 0 and (spread / mid) > 0.50 and open_interest < 100:
                return GuardResult(status=GuardStatus.REJECT, reasons=["ZERO_BID_LOW_LIQUIDITY"])
            return GuardResult(status=GuardStatus.REJECT, reasons=["ZERO_BID_POST_MARKET"])
        return GuardResult(status=GuardStatus.REJECT, reasons=["ZERO_BID_NO_BUYER"])

    # 3. Dual Spread Gate (Relative + Absolute)
    rel_spread = (spread / mid) if mid > 0 else 1.0

    # Relative tiers: Pass <= 6%, Watch <= 10%, Reject > 10%
    if rel_spread <= 0.06:
        rel_tier = GuardStatus.PASS
    elif rel_spread <= 0.10:
        rel_tier = GuardStatus.WATCH
    else:
        rel_tier = GuardStatus.REJECT

    # Absolute tiers (half-spread): Pass <= $0.75, Watch <= $1.50, Reject > $1.50
    if half_spread <= 0.75:
        abs_tier = GuardStatus.PASS
    elif half_spread <= 1.50:
        abs_tier = GuardStatus.WATCH
    else:
        abs_tier = GuardStatus.REJECT

    # Combined relation:
    # Either entering Pass gives Pass; both Watch gives Watch; otherwise Reject
    if rel_tier == GuardStatus.PASS or abs_tier == GuardStatus.PASS:
        spread_status = GuardStatus.PASS
    elif rel_tier == GuardStatus.WATCH or abs_tier == GuardStatus.WATCH:
        spread_status = GuardStatus.WATCH
    else:
        spread_status = GuardStatus.REJECT
        reasons.append(f"SPREAD_TOO_WIDE_rel={rel_spread:.1%}_abs=${half_spread:.2f}")

    # 4. Open Interest (OI) Gate
    if open_interest >= 300:
        oi_status = GuardStatus.PASS
    elif open_interest >= 100:
        oi_status = GuardStatus.WATCH
    else:
        oi_status = GuardStatus.REJECT
        reasons.append(f"LOW_OI_{open_interest}")

    # 5. Activity Gate
    has_daily_vol = volume >= 50
    has_avg_vol = avg_volume_20d >= 20
    has_depth = (bid_size >= 10 or ask_size >= 10)

    if has_daily_vol or has_avg_vol or has_depth:
        act_status = GuardStatus.PASS
    elif volume >= 10 and open_interest >= 100:
        act_status = GuardStatus.WATCH
    else:
        act_status = GuardStatus.REJECT
        reasons.append(f"INSUFFICIENT_ACTIVITY_vol={volume}")

    # 6. Overall Result Determination
    statuses = [spread_status, oi_status, act_status]
    if GuardStatus.REJECT in statuses:
        return GuardResult(status=GuardStatus.REJECT, reasons=reasons)
    elif GuardStatus.WATCH in statuses:
        return GuardResult(status=GuardStatus.WATCH, reasons=reasons)
    else:
        return GuardResult(status=GuardStatus.PASS, reasons=[])
