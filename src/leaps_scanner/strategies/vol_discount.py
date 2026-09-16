"""
Strategy 2: Volatility Discount (Regime A & Regime B).
Screens long-dated LEAPS options when implied volatility is in a historical discount.
Adheres strictly to Defensive Clause 5 (ATM median IV, min 90d history, STRATEGY_DEGRADED fallback),
and Defensive Clause 2 (P_exec and liquidity guardrails).
"""
from dataclasses import dataclass, field
from typing import List, Optional
from src.leaps_scanner.core.metrics import calculate_carry_cost, calculate_effective_leverage
from src.leaps_scanner.strategies.guards import GuardStatus


@dataclass(frozen=True)
class VolDiscountUnderlyingMetrics:
    symbol: str
    spot: float
    pct_change_20d: float
    drawdown_52w_high: float
    current_atm_iv: float
    hv_252: float
    iv_percentile: float
    iv_rank: float
    iv_z_score: float
    valid_history_days: int
    is_degraded: bool
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class VolDiscountUnderlyingResult:
    symbol: str
    status: GuardStatus
    regime: str  # "REGIME_A", "REGIME_B", "NONE"
    iv_percentile: float
    iv_hv_ratio: float
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class VolDiscountContractResult:
    symbol: str
    status: GuardStatus
    regime: str
    effective_leverage: float
    carry_cost: float
    reasons: List[str] = field(default_factory=list)


def evaluate_vol_discount_underlying(
    metrics: VolDiscountUnderlyingMetrics
) -> VolDiscountUnderlyingResult:
    """
    Evaluate underlying for Volatility Discount using ATM IV history.
    Enforces two mutually exclusive regimes:
    1. Regime A: Bottoming low IV (non-crashing price action).
    2. Regime B: Post-crash relative value (IV still low relative to crisis).
    """
    reasons: List[str] = list(metrics.reasons)

    # 1. Defensive Clause 5: If history < 90 days, degrade strategy unconditionally
    if metrics.is_degraded or metrics.valid_history_days < 90:
        if "STRATEGY_DEGRADED_INSUFFICIENT_HISTORY" not in reasons:
            reasons.append("STRATEGY_DEGRADED_INSUFFICIENT_HISTORY")
        return VolDiscountUnderlyingResult(
            symbol=metrics.symbol,
            status=GuardStatus.REJECT,
            regime="NONE",
            iv_percentile=metrics.iv_percentile,
            iv_hv_ratio=0.0,
            reasons=reasons
        )

    iv_hv_ratio = (metrics.current_atm_iv / metrics.hv_252) if metrics.hv_252 > 0 else 1.0

    # Determine Regime: Mutually Exclusive
    is_crash = (metrics.pct_change_20d <= -0.15) or (metrics.drawdown_52w_high >= 0.20)

    if is_crash:
        # Regime B: Post-crash relative value
        # Condition: IV Percentile < 40% OR IV z-score < 0.0
        if metrics.iv_percentile < 0.40 or metrics.iv_z_score < 0.0:
            return VolDiscountUnderlyingResult(
                symbol=metrics.symbol,
                status=GuardStatus.PASS,
                regime="REGIME_B",
                iv_percentile=metrics.iv_percentile,
                iv_hv_ratio=iv_hv_ratio,
                reasons=reasons
            )
        else:
            reasons.append(f"REGIME_B_HIGH_IV_PERCENTILE_{metrics.iv_percentile:.1%}")
            return VolDiscountUnderlyingResult(
                symbol=metrics.symbol,
                status=GuardStatus.REJECT,
                regime="REGIME_B",
                iv_percentile=metrics.iv_percentile,
                iv_hv_ratio=iv_hv_ratio,
                reasons=reasons
            )
    else:
        # Regime A: Bottoming low IV
        # IV Percentile: Pass < 20%, Watch < 30%, Reject >= 30%
        if metrics.iv_percentile < 0.20:
            pct_tier = GuardStatus.PASS
        elif metrics.iv_percentile < 0.30:
            pct_tier = GuardStatus.WATCH
        else:
            pct_tier = GuardStatus.REJECT
            reasons.append(f"HIGH_IV_PERCENTILE_{metrics.iv_percentile:.1%}")

        # Long-end IV / HV252: Pass < 0.85, Watch < 1.00, Reject >= 1.00
        if iv_hv_ratio < 0.85:
            ratio_tier = GuardStatus.PASS
        elif iv_hv_ratio < 1.00:
            ratio_tier = GuardStatus.WATCH
        else:
            ratio_tier = GuardStatus.REJECT
            reasons.append(f"HIGH_IV_HV_RATIO_{iv_hv_ratio:.2f}")

        if pct_tier == GuardStatus.PASS and ratio_tier in (GuardStatus.PASS, GuardStatus.WATCH):
            status = GuardStatus.PASS
        elif pct_tier in (GuardStatus.PASS, GuardStatus.WATCH) and ratio_tier in (GuardStatus.PASS, GuardStatus.WATCH):
            status = GuardStatus.WATCH
        else:
            status = GuardStatus.REJECT

        return VolDiscountUnderlyingResult(
            symbol=metrics.symbol,
            status=status,
            regime="REGIME_A",
            iv_percentile=metrics.iv_percentile,
            iv_hv_ratio=iv_hv_ratio,
            reasons=reasons
        )


def evaluate_vol_discount_contract(
    underlying_result: VolDiscountUnderlyingResult,
    spot: float,
    strike: float,
    dte: float,
    p_exec: float,
    delta: float,
    liquidity_status: GuardStatus = GuardStatus.PASS,
    dividend_yield: float = 0.0,
    days_to_earnings: Optional[int] = None
) -> VolDiscountContractResult:
    """
    Evaluate specific contract for Volatility Discount strategy.
    """
    reasons: List[str] = list(underlying_result.reasons)

    # 1. Underlying pass check
    if underlying_result.status == GuardStatus.REJECT:
        return VolDiscountContractResult(
            symbol=underlying_result.symbol,
            status=GuardStatus.REJECT,
            regime=underlying_result.regime,
            effective_leverage=0.0,
            carry_cost=0.0,
            reasons=reasons
        )

    # 2. Liquidity check
    if liquidity_status == GuardStatus.REJECT:
        reasons.append("LIQUIDITY_REJECTED")
        return VolDiscountContractResult(
            symbol=underlying_result.symbol,
            status=GuardStatus.REJECT,
            regime=underlying_result.regime,
            effective_leverage=0.0,
            carry_cost=0.0,
            reasons=reasons
        )

    # 3. DTE check: Pass >= 300, Watch [250, 300), Reject < 250
    if dte < 250.0:
        reasons.append(f"INSUFFICIENT_DTE_{dte}")
        return VolDiscountContractResult(
            symbol=underlying_result.symbol,
            status=GuardStatus.REJECT,
            regime=underlying_result.regime,
            effective_leverage=0.0,
            carry_cost=0.0,
            reasons=reasons
        )
    dte_status = GuardStatus.PASS if dte >= 300.0 else GuardStatus.WATCH

    # 4. Strike window check: K in [0.70S, 1.25S]
    if spot > 0:
        strike_ratio = strike / spot
        if strike_ratio < 0.70 or strike_ratio > 1.25:
            reasons.append(f"STRIKE_OUT_OF_WINDOW_{strike_ratio:.2f}")
            return VolDiscountContractResult(
                symbol=underlying_result.symbol,
                status=GuardStatus.REJECT,
                regime=underlying_result.regime,
                effective_leverage=0.0,
                carry_cost=0.0,
                reasons=reasons
            )

    carry_res = calculate_carry_cost(
        spot=spot,
        strike=strike,
        dte=dte,
        p_exec=p_exec,
        dividend_yield=dividend_yield
    )
    leverage = calculate_effective_leverage(delta=delta, spot=spot, p_exec=p_exec)

    final_status = underlying_result.status
    if liquidity_status == GuardStatus.WATCH or dte_status == GuardStatus.WATCH:
        final_status = GuardStatus.WATCH

    # 5. Earnings event window check: if in Regime A and earnings within 10 days, downgrade to WATCH
    if days_to_earnings is not None and 0 <= days_to_earnings <= 10:
        reasons.append("EVENT_WINDOW")
        if underlying_result.regime == "REGIME_A":
            final_status = GuardStatus.WATCH

    return VolDiscountContractResult(
        symbol=underlying_result.symbol,
        status=final_status,
        regime=underlying_result.regime,
        effective_leverage=leverage,
        carry_cost=carry_res.total_annualized_carry,
        reasons=reasons
    )
