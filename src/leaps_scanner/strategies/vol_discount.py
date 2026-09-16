"""
Strategy 2: Volatility Discount (Regime A & Regime B).
Screens long-dated LEAPS options when implied volatility is in a historical discount.
Adheres strictly to Defensive Clause 5 (ATM median IV, min 90d history, STRATEGY_DEGRADED fallback),
and Defensive Clause 2 (P_exec and liquidity guardrails).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.leaps_scanner.core.metrics import calculate_carry_cost, calculate_effective_leverage
from src.leaps_scanner.strategies.guards import GuardStatus, fold_gates
from src.leaps_scanner.data.universe import SymbologyNormalizer


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
    hv_20: float = 0.0
    hv_percentile: Optional[float] = None
    hv_z_score: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class VolDiscountUnderlyingResult:
    symbol: str
    status: GuardStatus
    regime: str  # "REGIME_A", "REGIME_B", "NONE"
    iv_percentile: float
    iv_hv_ratio: float
    reasons: List[str] = field(default_factory=list)
    gates: Dict[str, GuardStatus] = field(default_factory=dict)


@dataclass(frozen=True)
class VolDiscountContractResult:
    symbol: str
    status: GuardStatus
    regime: str
    effective_leverage: float
    carry_cost: float
    reasons: List[str] = field(default_factory=list)
    gates: Dict[str, GuardStatus] = field(default_factory=dict)


def _cap_watch(status: GuardStatus) -> GuardStatus:
    return GuardStatus.WATCH if status == GuardStatus.PASS else status


def _evaluate_realized_vol_proxy(
    symbol: str,
    metrics: VolDiscountUnderlyingMetrics,
    is_crash: bool,
    reasons: List[str],
) -> VolDiscountUnderlyingResult:
    """HV20 percentile + HV20/HV252 stand in for IV until the warehouse has 90 days. Never PASS."""
    reasons = list(reasons)
    if "REALIZED_VOL_PROXY" not in reasons:
        reasons.append("REALIZED_VOL_PROXY")
    hv_ratio = (metrics.hv_20 / metrics.hv_252) if metrics.hv_252 > 0 else 1.0
    hv_pct = float(metrics.hv_percentile)

    if is_crash:
        if hv_pct < 0.40 or metrics.hv_z_score < 0.0:
            status = GuardStatus.WATCH
        else:
            status = GuardStatus.REJECT
            reasons.append(f"REGIME_B_HIGH_HV_PERCENTILE_{hv_pct:.1%}")
        gates = {
            "iv_history": GuardStatus.WATCH,
            "hv_proxy": status,
            "regime_b": status,
            "iv_percentile": GuardStatus.PASS,
            "iv_hv": GuardStatus.PASS,
            "crash": GuardStatus.WATCH,
        }
        return VolDiscountUnderlyingResult(
            symbol=symbol,
            status=_cap_watch(status) if status != GuardStatus.REJECT else status,
            regime="REGIME_B_HV",
            iv_percentile=hv_pct,
            iv_hv_ratio=hv_ratio,
            reasons=reasons,
            gates=gates,
        )

    if hv_pct < 0.20:
        pct_tier = GuardStatus.PASS
    elif hv_pct < 0.30:
        pct_tier = GuardStatus.WATCH
    else:
        pct_tier = GuardStatus.REJECT
        reasons.append(f"HIGH_HV_PERCENTILE_{hv_pct:.1%}")

    if hv_ratio < 0.85:
        ratio_tier = GuardStatus.PASS
    elif hv_ratio < 1.00:
        ratio_tier = GuardStatus.WATCH
    else:
        ratio_tier = GuardStatus.REJECT
        reasons.append(f"HIGH_HV_HV_RATIO_{hv_ratio:.2f}")

    if pct_tier == GuardStatus.REJECT or ratio_tier == GuardStatus.REJECT:
        status = GuardStatus.REJECT
    else:
        status = GuardStatus.WATCH

    return VolDiscountUnderlyingResult(
        symbol=symbol,
        status=status,
        regime="REGIME_A_HV",
        iv_percentile=hv_pct,
        iv_hv_ratio=hv_ratio,
        reasons=reasons,
        gates={
            "iv_history": GuardStatus.WATCH,
            "hv_proxy": status,
            "iv_percentile": pct_tier,
            "iv_hv": ratio_tier,
            "regime_b": GuardStatus.PASS,
            "crash": GuardStatus.PASS,
        },
    )


def evaluate_vol_discount_underlying(
    metrics: VolDiscountUnderlyingMetrics
) -> VolDiscountUnderlyingResult:
    """
    Evaluate underlying for Volatility Discount using ATM IV history.
    Enforces two mutually exclusive regimes:
    1. Regime A: Bottoming low IV (non-crashing price action).
    2. Regime B: Post-crash relative value (IV still low relative to crisis).
    """
    canonical_sym = SymbologyNormalizer.to_canonical(metrics.symbol)
    reasons: List[str] = list(metrics.reasons)

    is_crash = (metrics.pct_change_20d <= -0.15) or (metrics.drawdown_52w_high >= 0.20)

    # Clause 5: <90d ATM IV → realized-vol proxy, never PASS.
    if metrics.is_degraded or metrics.valid_history_days < 90:
        if "STRATEGY_DEGRADED_INSUFFICIENT_HISTORY" not in reasons:
            reasons.append("STRATEGY_DEGRADED_INSUFFICIENT_HISTORY")
        if metrics.hv_percentile is None or metrics.hv_252 <= 0:
            return VolDiscountUnderlyingResult(
                symbol=canonical_sym,
                status=GuardStatus.REJECT,
                regime="NONE",
                iv_percentile=metrics.iv_percentile,
                iv_hv_ratio=0.0,
                reasons=reasons,
                gates={"iv_history": GuardStatus.REJECT, "hv_proxy": GuardStatus.REJECT},
            )
        return _evaluate_realized_vol_proxy(canonical_sym, metrics, is_crash, reasons)

    iv_hv_ratio = (metrics.current_atm_iv / metrics.hv_252) if metrics.hv_252 > 0 else 1.0

    if is_crash:
        # Regime B: Post-crash relative value
        # Condition: IV Percentile < 40% OR IV z-score < 0.0
        if metrics.iv_percentile < 0.40 or metrics.iv_z_score < 0.0:
            return VolDiscountUnderlyingResult(
                symbol=canonical_sym,
                status=GuardStatus.PASS,
                regime="REGIME_B",
                iv_percentile=metrics.iv_percentile,
                iv_hv_ratio=iv_hv_ratio,
                reasons=reasons,
                gates={
                    "iv_history": GuardStatus.PASS,
                    "regime_b": GuardStatus.PASS,
                    "crash": GuardStatus.WATCH,
                    "iv_percentile": GuardStatus.PASS,
                    "iv_hv": GuardStatus.PASS,
                    "hv_proxy": GuardStatus.PASS,
                },
            )
        else:
            reasons.append(f"REGIME_B_HIGH_IV_PERCENTILE_{metrics.iv_percentile:.1%}")
            return VolDiscountUnderlyingResult(
                symbol=canonical_sym,
                status=GuardStatus.REJECT,
                regime="REGIME_B",
                iv_percentile=metrics.iv_percentile,
                iv_hv_ratio=iv_hv_ratio,
                reasons=reasons,
                gates={
                    "iv_history": GuardStatus.PASS,
                    "regime_b": GuardStatus.REJECT,
                    "crash": GuardStatus.WATCH,
                    "iv_percentile": GuardStatus.REJECT,
                    "iv_hv": GuardStatus.PASS,
                    "hv_proxy": GuardStatus.PASS,
                },
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
            symbol=canonical_sym,
            status=status,
            regime="REGIME_A",
            iv_percentile=metrics.iv_percentile,
            iv_hv_ratio=iv_hv_ratio,
            reasons=reasons,
            gates={
                "iv_history": GuardStatus.PASS,
                "iv_percentile": pct_tier,
                "iv_hv": ratio_tier,
                "hv_proxy": GuardStatus.PASS,
                "regime_b": GuardStatus.PASS,
                "crash": GuardStatus.PASS,
            },
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
    Always fills per-filter gates so the dashboard can toggle them independently.
    """
    reasons: List[str] = list(underlying_result.reasons)
    gates: Dict[str, GuardStatus] = dict(underlying_result.gates)

    if liquidity_status == GuardStatus.REJECT:
        reasons.append("LIQUIDITY_REJECTED")
    gates["liquidity"] = liquidity_status

    if dte < 250.0:
        dte_status = GuardStatus.REJECT
        reasons.append(f"INSUFFICIENT_DTE_{dte}")
    elif dte >= 300.0:
        dte_status = GuardStatus.PASS
    else:
        dte_status = GuardStatus.WATCH
    gates["dte"] = dte_status

    strike_status = GuardStatus.PASS
    if spot > 0:
        strike_ratio = strike / spot
        if strike_ratio < 0.70 or strike_ratio > 1.25:
            strike_status = GuardStatus.REJECT
            reasons.append(f"STRIKE_OUT_OF_WINDOW_{strike_ratio:.2f}")
    gates["strike"] = strike_status

    earn_status = GuardStatus.PASS
    if days_to_earnings is not None and 0 <= days_to_earnings <= 10:
        reasons.append("EVENT_WINDOW")
        if underlying_result.regime in ("REGIME_A", "REGIME_A_HV"):
            earn_status = GuardStatus.WATCH
    gates["earnings"] = earn_status

    carry_res = calculate_carry_cost(
        spot=spot,
        strike=strike,
        dte=dte,
        p_exec=p_exec,
        dividend_yield=dividend_yield
    )
    leverage = calculate_effective_leverage(delta=delta, spot=spot, p_exec=p_exec)

    return VolDiscountContractResult(
        symbol=underlying_result.symbol,
        status=fold_gates(gates),
        regime=underlying_result.regime,
        effective_leverage=leverage,
        carry_cost=carry_res.total_annualized_carry,
        reasons=reasons,
        gates=gates,
    )
