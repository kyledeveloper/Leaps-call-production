"""
Strategy 3: Blue-Chip Oversold Confluence.
Combines multiple medium-term technical indicators (RSI, 200DMA, 52w High/Low) on high-liquidity blue-chips.
Adheres strictly to Core Universe Hard Constraint, Defensive Clause 6 (min 200 daily bars),
and Defensive Clause 2 (P_exec & liquidity guards).
"""
from dataclasses import dataclass, field
from typing import List, Optional, Set
from src.leaps_scanner.core.metrics import calculate_carry_cost, calculate_effective_leverage
from src.leaps_scanner.strategies.guards import GuardStatus
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


@dataclass(frozen=True)
class OversoldContractResult:
    symbol: str
    status: GuardStatus
    confluence_score: float
    effective_leverage: float
    carry_cost: float
    intrinsic_ratio: float
    reasons: List[str] = field(default_factory=list)


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

    # 1. Hard constraint: Core universe check (with canonical normalization)
    canonical_sym = SymbologyNormalizer.to_canonical(metrics.symbol)
    if canonical_sym not in universe:
        return OversoldUnderlyingResult(
            symbol=canonical_sym,
            status=GuardStatus.REJECT,
            confluence_score=0.0,
            core_signal_count=0,
            weak_signal_count=0,
            reasons=["NOT_IN_CORE_UNIVERSE"]
        )

    # 2. Daily bars sufficiency check
    if not metrics.is_valid or metrics.bar_count < 200:
        return OversoldUnderlyingResult(
            symbol=canonical_sym,
            status=GuardStatus.REJECT,
            confluence_score=0.0,
            core_signal_count=0,
            weak_signal_count=0,
            reasons=list(metrics.reasons) if metrics.reasons else ["INSUFFICIENT_DAILY_BARS"]
        )

    core_signals = 0
    weak_signals = 0
    score = 0.0

    # Signal 1: RSI(14)
    # Core <= 30 (1.0), Weak <= 40 (0.5)
    if metrics.rsi_14 <= 30.0:
        score += 1.0
        core_signals += 1
    elif metrics.rsi_14 <= 40.0:
        score += 0.5
        weak_signals += 1

    # Signal 2: Relative to 200DMA
    # Mega-cap/ETF: <= -10% (1.0), <= -6% (0.5)
    # High-volatility (HV20 > 40%): <= -15% (1.0), <= -8% (0.5)
    if metrics.hv_20 > 0.40:
        if metrics.pct_to_200dma <= -0.15:
            score += 1.0
            core_signals += 1
        elif metrics.pct_to_200dma <= -0.08:
            score += 0.5
            weak_signals += 1
    else:
        if metrics.pct_to_200dma <= -0.10:
            score += 1.0
            core_signals += 1
        elif metrics.pct_to_200dma <= -0.06:
            score += 0.5
            weak_signals += 1

    # Signal 3: Drawdown from 52-week High
    # Core >= 15% (1.0), Weak >= 10% (0.5)
    if metrics.drawdown_52w_high >= 0.15:
        score += 1.0
        core_signals += 1
    elif metrics.drawdown_52w_high >= 0.10:
        score += 0.5
        weak_signals += 1

    # Signal 4: Distance from 52-week Low (only evaluated when experiencing pullback/drawdown)
    # Core: Bounce >= 3% (1.0, avoids falling knife); Weak: near bottom < 3% (0.5)
    if metrics.drawdown_52w_high >= 0.10:
        if metrics.bounce_52w_low >= 0.03:
            score += 1.0
            core_signals += 1
        elif metrics.bounce_52w_low >= 0.0:
            score += 0.5
            weak_signals += 1

    # Overall Status:
    # Pass: Score >= 2.0 and at least 1 core signal
    # Watch: Score in [1.0, 2.0)
    # Reject: Score < 1.0
    if score >= 2.0 and core_signals >= 1:
        status = GuardStatus.PASS
    elif score >= 1.0:
        status = GuardStatus.WATCH
    else:
        status = GuardStatus.REJECT
        reasons.append(f"LOW_CONFLUENCE_SCORE_{score:.1f}")

    return OversoldUnderlyingResult(
        symbol=canonical_sym,
        status=status,
        confluence_score=score,
        core_signal_count=core_signals,
        weak_signal_count=weak_signals,
        reasons=reasons
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
    """
    reasons: List[str] = list(underlying_result.reasons)

    # 1. Underlying pass check
    if underlying_result.status == GuardStatus.REJECT:
        return OversoldContractResult(
            symbol=underlying_result.symbol,
            status=GuardStatus.REJECT,
            confluence_score=underlying_result.confluence_score,
            effective_leverage=0.0,
            carry_cost=0.0,
            intrinsic_ratio=0.0,
            reasons=reasons
        )

    # 2. Shared liquidity guard check
    if liquidity_status == GuardStatus.REJECT:
        reasons.append("LIQUIDITY_REJECTED")
        return OversoldContractResult(
            symbol=underlying_result.symbol,
            status=GuardStatus.REJECT,
            confluence_score=underlying_result.confluence_score,
            effective_leverage=0.0,
            carry_cost=0.0,
            intrinsic_ratio=0.0,
            reasons=reasons
        )

    # 3. DTE check (>= 250)
    if dte < 250.0:
        reasons.append(f"INSUFFICIENT_DTE_{dte}")
        return OversoldContractResult(
            symbol=underlying_result.symbol,
            status=GuardStatus.REJECT,
            confluence_score=underlying_result.confluence_score,
            effective_leverage=0.0,
            carry_cost=0.0,
            intrinsic_ratio=0.0,
            reasons=reasons
        )

    # 4. Strike window check: [0.50S, 1.25S]
    if spot > 0:
        strike_ratio = strike / spot
        if strike_ratio < 0.50 or strike_ratio > 1.25:
            reasons.append(f"STRIKE_OUT_OF_WINDOW_{strike_ratio:.2f}")
            return OversoldContractResult(
                symbol=underlying_result.symbol,
                status=GuardStatus.REJECT,
                confluence_score=underlying_result.confluence_score,
                effective_leverage=0.0,
                carry_cost=0.0,
                intrinsic_ratio=0.0,
                reasons=reasons
            )

    # 5. Earnings window tagging
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

    # Final contract tier: reflects underlying tier and liquidity tier
    if underlying_result.status == GuardStatus.WATCH or liquidity_status == GuardStatus.WATCH:
        final_status = GuardStatus.WATCH
    else:
        final_status = GuardStatus.PASS

    return OversoldContractResult(
        symbol=underlying_result.symbol,
        status=final_status,
        confluence_score=underlying_result.confluence_score,
        effective_leverage=leverage,
        carry_cost=carry_res.total_annualized_carry,
        intrinsic_ratio=intrinsic_ratio,
        reasons=reasons
    )
