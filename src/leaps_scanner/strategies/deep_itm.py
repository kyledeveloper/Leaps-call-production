"""
Strategy 1: Deep ITM Stock Replacement / PMCC Base Layer.
Screens far-dated deep in-the-money call options with low carry drag and 2.5x-4.5x effective leverage.
Adheres strictly to Defensive Clause 2 (P_exec) and Clause 4 (operates even if IV is None).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.leaps_scanner.core.metrics import YEAR_DAYS, calculate_carry_cost, calculate_effective_leverage
from src.leaps_scanner.core.greeks import calculate_american_greeks
from src.leaps_scanner.core.rates import RateCurve
from src.leaps_scanner.data.funnel import STRATEGY_STRIKE_RATIOS
from src.leaps_scanner.strategies.guards import GuardStatus, evaluate_liquidity_guard, fold_gates

_RATE_CURVE = RateCurve()


@dataclass(frozen=True)
class StrategyResult:
    status: GuardStatus
    reasons: List[str] = field(default_factory=list)
    delta: float = 0.0
    intrinsic_ratio: float = 0.0
    effective_leverage: float = 0.0
    carry_cost: float = 0.0
    theta_daily_pct: float = 0.0
    gates: Dict[str, GuardStatus] = field(default_factory=dict)

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
    iv: Optional[float] = None,
    bid: float = 0.0,
    ask: float = 0.0,
    open_interest: int = 0,
    volume: int = 0,
    bid_size: int = 0,
    ask_size: int = 10,
) -> StrategyResult:
    """
    Evaluate contract against Deep ITM Stock Replacement criteria.
    Operates on intrinsic, P_exec, and Delta; does NOT require IV.
    """
    reasons: List[str] = []

    if spot <= 0 or strike <= 0 or p_exec <= 0:
        return StrategyResult(
            status=GuardStatus.REJECT,
            reasons=["INVALID_NUMERICS"],
            gates={"strike": GuardStatus.REJECT},
        )

    intrinsic = max(0.0, spot - strike)
    intrinsic_ratio = intrinsic / p_exec

    carry_res = calculate_carry_cost(
        spot=spot,
        strike=strike,
        dte=dte,
        p_exec=p_exec,
        dividend_yield=dividend_yield
    )
    # Strategy-1 drag: annualized time value only. Do not add q (already in the forward).
    carry = carry_res.annualized_extrinsic_rate
    leverage = calculate_effective_leverage(delta=delta, spot=spot, p_exec=p_exec)

    # 0. Strike window [0.65S, 0.85S]
    low_r, high_r = STRATEGY_STRIKE_RATIOS["deep_itm"]
    strike_ratio = strike / spot
    if low_r <= strike_ratio <= high_r:
        strike_tier = GuardStatus.PASS
    else:
        strike_tier = GuardStatus.REJECT
        reasons.append(f"STRIKE_OUT_OF_WINDOW_{strike_ratio:.2f}")

    # 1. Delta: Pass [0.70, 0.85], Watch (0.85, 0.90], Reject <0.70 or >0.90
    if 0.70 <= delta <= 0.85:
        delta_tier = GuardStatus.PASS
    elif 0.85 < delta <= 0.90:
        delta_tier = GuardStatus.WATCH
    else:
        delta_tier = GuardStatus.REJECT
        reasons.append("DELTA_OUT_OF_BOUNDS")

    # 2. Intrinsic / P_exec: Pass >= 65%, Watch >= 55%, Reject < 55%
    if intrinsic_ratio >= 0.65:
        ratio_tier = GuardStatus.PASS
    elif intrinsic_ratio >= 0.55:
        ratio_tier = GuardStatus.WATCH
    else:
        ratio_tier = GuardStatus.REJECT
        reasons.append(f"LOW_INTRINSIC_RATIO_{intrinsic_ratio:.1%}")

    # 3. Leverage: Pass [2.5, 4.5], Watch (4.5, 5.5] or [2.0, 2.5), Reject >5.5 or <2.0
    if 2.5 <= leverage <= 4.5:
        lev_tier = GuardStatus.PASS
    elif 2.0 <= leverage < 2.5 or 4.5 < leverage <= 5.5:
        lev_tier = GuardStatus.WATCH
    else:
        lev_tier = GuardStatus.REJECT
        reasons.append(f"LEVERAGE_OUT_OF_BOUNDS_{leverage:.2f}")

    # 4. Time-value drag: extrinsic / (P_exec * T)
    if carry < 0.15:
        carry_tier = GuardStatus.PASS
    elif carry < 0.25:
        carry_tier = GuardStatus.WATCH
    elif carry < 0.35:
        carry_tier = GuardStatus.WATCH
        reasons.append(f"HIGH_CARRY_ELEVATED_{carry:.1%}")
    else:
        carry_tier = GuardStatus.REJECT
        reasons.append(f"HIGH_CARRY_DRAG_{carry:.1%}")

    # 5. DTE: Pass >= 300, Watch [250, 300), Reject < 250
    if dte >= 300.0:
        dte_tier = GuardStatus.PASS
    elif dte >= 250.0:
        dte_tier = GuardStatus.WATCH
    else:
        dte_tier = GuardStatus.REJECT
        reasons.append(f"INSUFFICIENT_DTE_{dte}")

    # 6. Daily theta decay as |Θ_day| / P_exec. Skip if IV missing (Clause 4).
    theta_daily_pct = 0.0
    theta_tier: Optional[GuardStatus] = None
    if iv is not None and iv > 0 and p_exec > 0 and dte > 0:
        t_years = dte / YEAR_DAYS
        try:
            greeks = calculate_american_greeks(
                spot=spot,
                strike=strike,
                t=t_years,
                r=_RATE_CURVE.get_rate(t_years),
                q=max(0.0, dividend_yield),
                sigma=max(float(iv), 0.05),
            )
            theta_daily_pct = abs(greeks.theta_daily) / p_exec
        except Exception:
            theta_daily_pct = 0.0
            theta_tier = None
        else:
            if theta_daily_pct < 0.0003:
                theta_tier = GuardStatus.PASS
            elif theta_daily_pct < 0.0008:
                theta_tier = GuardStatus.WATCH
            elif theta_daily_pct < 0.0010:
                theta_tier = GuardStatus.WATCH
                reasons.append(f"HIGH_THETA_ELEVATED_{theta_daily_pct:.3%}/d")
            else:
                theta_tier = GuardStatus.REJECT
                reasons.append(f"HIGH_THETA_DRAG_{theta_daily_pct:.3%}/d")

    gates: Dict[str, GuardStatus] = {
        "strike": strike_tier,
        "delta": delta_tier,
        "intrinsic": ratio_tier,
        "leverage": lev_tier,
        "carry": carry_tier,
        "dte": dte_tier,
        "theta": GuardStatus.PASS,
        "oi": GuardStatus.PASS,
        "spread": GuardStatus.PASS,
        "volume": GuardStatus.PASS,
    }
    if theta_tier is not None:
        gates["theta"] = theta_tier

    # 6. Shared liquidity split into OI / spread / volume for independent toggles.
    if ask > 0:
        liq = evaluate_liquidity_guard(
            bid=bid,
            ask=ask,
            open_interest=open_interest,
            volume=volume,
            bid_size=bid_size,
            ask_size=ask_size,
        )
        gates["spread"] = liq.spread_status
        gates["oi"] = liq.oi_status
        gates["volume"] = liq.volume_status
        reasons.extend(liq.reasons)

    status = fold_gates(gates)

    return StrategyResult(
        status=status,
        reasons=reasons,
        delta=delta,
        intrinsic_ratio=intrinsic_ratio,
        effective_leverage=leverage,
        carry_cost=carry,
        theta_daily_pct=theta_daily_pct,
        gates=gates,
    )
