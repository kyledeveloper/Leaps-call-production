"""
Strategy 3: Blue-Chip Oversold Confluence.
Combines multiple medium-term technical indicators (RSI, 200DMA, 52w High/Low) on high-liquidity blue-chips.
Adheres strictly to Core Universe Hard Constraint, Defensive Clause 6 (min 200 daily bars),
and Defensive Clause 2 (P_exec & liquidity guards).
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from src.leaps_scanner.core.metrics import calculate_carry_cost, calculate_effective_leverage
from src.leaps_scanner.strategies.guards import GuardStatus, fold_gates, signal_tier
from src.leaps_scanner.data.universe import FULL_CORE_UNIVERSE, SymbologyNormalizer

DEFAULT_CORE_UNIVERSE: Set[str] = FULL_CORE_UNIVERSE


@dataclass(frozen=True)
class OversoldUnderlyingMetrics:
    symbol: str
    spot: float
    rsi_14: float
    pct_to_200dma: float
    drawdown_52w_high: float
    bounce_52w_low: float
    hv_20: float
    bar_count: int
    is_valid: bool = True
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class OversoldUnderlyingResult:
    symbol: str
    status: GuardStatus
    confluence_score: float
    core_signal_count: int
    weak_signal_count: int
    reasons: List[str] = field(default_factory=list)
    gates: Dict[str, GuardStatus] = field(default_factory=dict)
    signal_points: Dict[str, float] = field(default_factory=dict)
    signal_core: Dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class OversoldContractResult:
    symbol: str
    status: GuardStatus
    confluence_score: float
    effective_leverage: float
    carry_cost: float
    intrinsic_ratio: float
    reasons: List[str] = field(default_factory=list)
    gates: Dict[str, GuardStatus] = field(default_factory=dict)
    signal_points: Dict[str, float] = field(default_factory=dict)
    signal_core: Dict[str, bool] = field(default_factory=dict)


def evaluate_oversold_underlying(
    metrics: OversoldUnderlyingMetrics,
    core_universe: Optional[Set[str]] = None
) -> OversoldUnderlyingResult:
    """
    Evaluate underlying against Strategy 3 technical confluence rules.
    Requires membership in core universe and minimum 200 daily price bars.
    """
    universe = core_universe if core_universe is not None else DEFAULT_CORE_UNIVERSE
    reasons: List[str] = []
    canonical_sym = SymbologyNormalizer.to_canonical(metrics.symbol)

    in_universe = canonical_sym in universe
    if not in_universe:
        reasons.append("NOT_IN_CORE_UNIVERSE")
    bars_ok = metrics.is_valid and metrics.bar_count >= 200
    if not bars_ok:
        reasons.extend(list(metrics.reasons) if metrics.reasons else ["INSUFFICIENT_DAILY_BARS"])

    core_signals = 0
    weak_signals = 0
    score = 0.0
    rsi_pts = dma_pts = dma_hv_pts = dd_pts = bounce_pts = 0.0
    rsi_core = dma_core = dma_hv_core = dd_core = bounce_core = False

    if metrics.rsi_14 <= 30.0:
        rsi_pts, rsi_core, score, core_signals = 1.0, True, score + 1.0, core_signals + 1
    elif metrics.rsi_14 <= 40.0:
        rsi_pts, score, weak_signals = 0.5, score + 0.5, weak_signals + 1

    if metrics.hv_20 > 0.40:
        if metrics.pct_to_200dma <= -0.15:
            dma_hv_pts, dma_hv_core, score, core_signals = 1.0, True, score + 1.0, core_signals + 1
        elif metrics.pct_to_200dma <= -0.08:
            dma_hv_pts, score, weak_signals = 0.5, score + 0.5, weak_signals + 1
    else:
        if metrics.pct_to_200dma <= -0.10:
            dma_pts, dma_core, score, core_signals = 1.0, True, score + 1.0, core_signals + 1
        elif metrics.pct_to_200dma <= -0.06:
            dma_pts, score, weak_signals = 0.5, score + 0.5, weak_signals + 1

    if metrics.drawdown_52w_high >= 0.15:
        dd_pts, dd_core, score, core_signals = 1.0, True, score + 1.0, core_signals + 1
    elif metrics.drawdown_52w_high >= 0.10:
        dd_pts, score, weak_signals = 0.5, score + 0.5, weak_signals + 1

    if metrics.drawdown_52w_high >= 0.10:
        if metrics.bounce_52w_low >= 0.03:
            bounce_pts, bounce_core, score, core_signals = 1.0, True, score + 1.0, core_signals + 1
        elif metrics.bounce_52w_low >= 0.0:
            bounce_pts, score, weak_signals = 0.5, score + 0.5, weak_signals + 1

    if score >= 2.0 and core_signals >= 1:
        conf_status = GuardStatus.PASS
    elif score >= 1.0:
        conf_status = GuardStatus.WATCH
    else:
        conf_status = GuardStatus.REJECT
        reasons.append(f"LOW_CONFLUENCE_SCORE_{score:.1f}")

    gates = {
        "universe": GuardStatus.PASS if in_universe else GuardStatus.REJECT,
        "bars": GuardStatus.PASS if bars_ok else GuardStatus.REJECT,
        "rsi": signal_tier(rsi_core, rsi_pts > 0),
        "dma": signal_tier(dma_core, dma_pts > 0) if metrics.hv_20 <= 0.40 else GuardStatus.PASS,
        "dma_hv": signal_tier(dma_hv_core, dma_hv_pts > 0) if metrics.hv_20 > 0.40 else GuardStatus.PASS,
        "drawdown": signal_tier(dd_core, dd_pts > 0),
        "bounce": signal_tier(bounce_core, bounce_pts > 0) if metrics.drawdown_52w_high >= 0.10 else GuardStatus.PASS,
        "confluence": conf_status,
    }
    status = fold_gates({"universe": gates["universe"], "bars": gates["bars"], "confluence": conf_status})

    return OversoldUnderlyingResult(
        symbol=canonical_sym,
        status=status,
        confluence_score=score,
        core_signal_count=core_signals,
        weak_signal_count=weak_signals,
        reasons=reasons,
        gates=gates,
        signal_points={"rsi": rsi_pts, "dma": dma_pts, "dma_hv": dma_hv_pts, "drawdown": dd_pts, "bounce": bounce_pts},
        signal_core={"rsi": rsi_core, "dma": dma_core, "dma_hv": dma_hv_core, "drawdown": dd_core, "bounce": bounce_core},
    )


def evaluate_oversold_contract(
    underlying_result: OversoldUnderlyingResult,
    spot: float,
    strike: float,
    dte: float,
    p_exec: float,
    delta: float,
    liquidity_status: GuardStatus = GuardStatus.PASS,
    dividend_yield: float = 0.0,
    days_to_earnings: Optional[int] = None
) -> OversoldContractResult:
    """
    Evaluate specific LEAPS call contract for an oversold candidate underlying.
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
    else:
        dte_status = GuardStatus.PASS
    gates["dte"] = dte_status

    strike_status = GuardStatus.PASS
    if spot > 0:
        strike_ratio = strike / spot
        if strike_ratio < 0.50 or strike_ratio > 1.25:
            strike_status = GuardStatus.REJECT
            reasons.append(f"STRIKE_OUT_OF_WINDOW_{strike_ratio:.2f}")
    gates["strike"] = strike_status

    if days_to_earnings is not None and 0 <= days_to_earnings <= 10:
        reasons.append("EVENT_WINDOW")

    intrinsic = max(0.0, spot - strike)
    intrinsic_ratio = (intrinsic / p_exec) if p_exec > 0 else 0.0
    carry_res = calculate_carry_cost(
        spot=spot,
        strike=strike,
        dte=dte,
        p_exec=p_exec,
        dividend_yield=dividend_yield
    )
    leverage = calculate_effective_leverage(delta=delta, spot=spot, p_exec=p_exec)

    # Leverage gate (same tiers as deep_itm): extreme effective leverage turns
    # the position into a lottery ticket, inconsistent with a bounce-capture stance.
    if 2.5 <= leverage <= 4.5:
        lev_tier = GuardStatus.PASS
    elif 2.0 <= leverage < 2.5 or 4.5 < leverage <= 5.5:
        lev_tier = GuardStatus.WATCH
    else:
        lev_tier = GuardStatus.REJECT
        reasons.append(f"LEVERAGE_OUT_OF_BOUNDS_{leverage:.2f}")
    gates["leverage"] = lev_tier

    # Carry gate (same tiers as deep_itm): annualized time-value drag as a
    # fraction of deployed capital, plus dividend yield. Unlike the vol
    # discount board, this is a directional bounce play, so carry drag
    # directly erodes the expected move and must be screened.
    carry = carry_res.total_annualized_carry
    if not math.isfinite(carry) or carry < 0.0:
        carry = 999.0
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
    gates["carry"] = carry_tier

    hard = {k: gates[k] for k in ("universe", "bars", "confluence", "strike", "dte", "liquidity", "leverage", "carry") if k in gates}
    return OversoldContractResult(
        symbol=underlying_result.symbol,
        status=fold_gates(hard),
        confluence_score=underlying_result.confluence_score,
        effective_leverage=leverage,
        carry_cost=carry_res.total_annualized_carry,
        intrinsic_ratio=intrinsic_ratio,
        reasons=reasons,
        gates=gates,
        signal_points=dict(underlying_result.signal_points),
        signal_core=dict(underlying_result.signal_core),
    )
