"""
Strategy 1: Deep ITM Stock Replacement / PMCC Base Layer.
Screens far-dated deep in-the-money call options with low carry drag and 2.5x-4.5x effective leverage.
Adheres strictly to Defensive Clause 2 (P_exec) and Clause 4 (operates even if IV is None).
"""
from dataclasses import dataclass, field
from typing import List, Optional
from src.leaps_scanner.core.metrics import YEAR_DAYS, calculate_carry_cost, calculate_effective_leverage
from src.leaps_scanner.strategies.guards import GuardStatus


@dataclass(frozen=True)
class StrategyResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    delta: float = 0.0
    intrinsic_ratio: float = 0.0
    effective_leverage: float = 0.0
    carry_cost: float = 0.0

    @property
    def is_rejected(self) -> bool:
        return self.status == GuardStatus.REJECT

    @property
    def is_pass(self) -> bool:
        return self.status == GuardStatus.PASS

    @property
    def is_watch(self) -> bool:
        return self.status == GuardStatus.WATCH


def evaluate_deep_itm(
    spot: float,
    strike: float,
    dte: float,
    p_exec: float,
    delta: float,
    dividend_yield: float = 0.0,
    iv: Optional[float] = None
) -> StrategyResult:
    """
    Evaluate contract against Deep ITM Stock Replacement criteria.
    Note: Operates strictly based on intrinsic value, P_exec, and Delta; does NOT require IV.
    """
    reasons: List[str] = []

    if spot <= 0 or strike <= 0 or p_exec <= 0:
        return StrategyResult(status=GuardStatus.REJECT, reasons=["INVALID_NUMERICS"])

    intrinsic = max(0.0, spot - strike)
    intrinsic_ratio = intrinsic / p_exec

    carry_res = calculate_carry_cost(
        spot=spot,
        strike=strike,
        dte=dte,
        p_exec=p_exec,
        dividend_yield=dividend_yield
    )
    carry = carry_res.total_annualized_carry
    leverage = calculate_effective_leverage(delta=delta, spot=spot, p_exec=p_exec)

    # 1. Delta Tier: Pass [0.75, 0.85], Watch [0.70, 0.75) or (0.85, 0.90], Reject <0.70 or >0.90
    if 0.75 <= delta <= 0.85:
        delta_tier = GuardStatus.PASS
    elif 0.70 <= delta < 0.75 or 0.85 < delta <= 0.90:
        delta_tier = GuardStatus.WATCH
    else:
        delta_tier = GuardStatus.REJECT
        reasons.append("DELTA_OUT_OF_BOUNDS")

    # 2. Intrinsic Ratio Tier: Pass >= 80%, Watch >= 70%, Reject < 70%
    if intrinsic_ratio >= 0.80:
        ratio_tier = GuardStatus.PASS
    elif intrinsic_ratio >= 0.70:
        ratio_tier = GuardStatus.WATCH
    else:
        ratio_tier = GuardStatus.REJECT
        reasons.append(f"LOW_INTRINSIC_RATIO_{intrinsic_ratio:.1%}")

    # 3. Leverage Tier: Pass [2.5, 4.5], Watch (4.5, 5.5] or [2.0, 2.5), Reject >5.5 or <2.0
    if 2.5 <= leverage <= 4.5:
        lev_tier = GuardStatus.PASS
    elif 2.0 <= leverage < 2.5 or 4.5 < leverage <= 5.5:
        lev_tier = GuardStatus.WATCH
    else:
        lev_tier = GuardStatus.REJECT
        reasons.append(f"LEVERAGE_OUT_OF_BOUNDS_{leverage:.2f}")

    # 4. Comprehensive Carry Cost Tier: Pass < 5%, Watch < 8%, Reject >= 8%
    if carry < 0.05:
        carry_tier = GuardStatus.PASS
    elif carry < 0.08:
        carry_tier = GuardStatus.WATCH
    else:
        carry_tier = GuardStatus.REJECT
        reasons.append(f"HIGH_CARRY_DRAG_{carry:.1%}")

    # 5. DTE Tier: Pass >= 300, Watch [250, 300), Reject < 250
    if dte >= 300.0:
        dte_tier = GuardStatus.PASS
    elif dte >= 250.0:
        dte_tier = GuardStatus.WATCH
    else:
        dte_tier = GuardStatus.REJECT
        reasons.append(f"INSUFFICIENT_DTE_{dte}")

    # Aggregate Tiers
    tiers = [delta_tier, ratio_tier, lev_tier, carry_tier, dte_tier]
    if GuardStatus.REJECT in tiers:
        status = GuardStatus.REJECT
    elif GuardStatus.WATCH in tiers:
        status = GuardStatus.WATCH
    else:
        status = GuardStatus.PASS

    return StrategyResult(
        status=status,
        reasons=reasons,
        delta=delta,
        intrinsic_ratio=intrinsic_ratio,
        effective_leverage=leverage,
        carry_cost=carry
    )
