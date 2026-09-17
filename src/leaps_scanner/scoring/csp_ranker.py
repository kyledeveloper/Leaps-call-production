"""
In-memory scoring and multi-board ranking module for Cash-Secured Puts (CSP).
Adheres to:
- DC-CSP-4: DTE hard floor, ROC vs AROC separation, Gamma penalty for short DTE
- DC-CSP-5: POP and -15% downside stress testing
- DC-CSP-6: Zero-bid rejection & sell-side P_exec
- DC-CSP-7: Concentration-capped contract recommendations & Max Loss
- DC-CSP-8: Ternary earnings state handling
- DC-CSP-9: Static/dynamic separation & lightweight re-ranking
- DC-CSP-10: Thread safety and immutable snapshot representation
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from src.leaps_scanner.core.metrics import (
    calculate_sell_pexec,
    calculate_roc,
    calculate_aroc,
    calculate_downside_buffer,
    calculate_pop,
    calculate_csp_capital_allocation,
    calculate_stress_test_pnl,
)
from src.leaps_scanner.strategies.csp_harvest import evaluate_csp_harvest
from src.leaps_scanner.strategies.csp_wheel import evaluate_csp_wheel
from src.leaps_scanner.strategies.csp_vol_rank import evaluate_csp_vol_rank
from src.leaps_scanner.strategies.guards import GuardStatus, evaluate_liquidity_guard


class EarningsStatus(str, Enum):
    CONFIRMED_SAFE = "CONFIRMED_SAFE"
    EARNINGS_IMPACTED = "EARNINGS_IMPACTED"
    EARNINGS_UNVERIFIED = "EARNINGS_UNVERIFIED"


@dataclass
class CSPCandidate:
    symbol: str
    underlying: str
    spot: float
    strike: float
    dte: float
    bid: float
    ask: float
    delta: float
    open_interest: int
    volume: int
    iv: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    rsi_14: float = 50.0
    pct_to_200dma: float = 0.0
    earnings_status: EarningsStatus = EarningsStatus.EARNINGS_UNVERIFIED
    bid_size: int = 10
    ask_size: int = 10
    days_to_earnings: Optional[int] = None
    is_etf: bool = False


@dataclass
class CSPFilterConfig:
    filter_aroc: bool = True
    min_aroc: float = 0.12
    filter_buffer: bool = True
    min_buffer: float = 0.03
    filter_ivr: bool = False
    min_ivr: float = 0.50
    filter_pop: bool = True
    min_pop: float = 0.70
    filter_earnings: bool = True
    strict_earnings: bool = False  # If True, also filter EARNINGS_UNVERIFIED
    filter_liquidity: bool = True
    max_capital_per_contract: Optional[float] = None
    max_underlying_exposure_pct: float = 0.25  # DC-CSP-7


@dataclass
class RankedCSPItem:
    candidate: CSPCandidate
    status: GuardStatus
    p_exec: float
    roc: float
    aroc: float
    buffer: float
    pop: float
    capital_info: dict
    stress_pnl: float
    score: float
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CSPBoardSnapshot:
    """
    DC-CSP-10: Thread-safe, immutable snapshot of CSP candidates and board results.
    Guarantees atomic pointer swap in AppState without lock contention.
    """
    timestamp: str
    alpha: float
    cash_pool: float
    candidates: Tuple[CSPCandidate, ...]
    boards: Dict[str, Tuple[RankedCSPItem, ...]]


def evaluate_csp_filters(
    candidate: CSPCandidate,
    config: CSPFilterConfig,
    p_exec: float,
    aroc: float,
    buffer: float,
    pop: float,
    capital_info: dict
) -> tuple[bool, List[str]]:
    """
    Check candidate against user toggleable filters.
    Returns (passes_all_filters, filter_reasons).
    """
    reasons = []

    # 1. Max Capital Per Contract
    if config.max_capital_per_contract is not None and config.max_capital_per_contract > 0:
        if capital_info["required_capital_per_contract"] > config.max_capital_per_contract:
            return False, ["EXCEEDS_MAX_CAPITAL_PER_CONTRACT"]

    # 2. AROC Filter
    if config.filter_aroc:
        if aroc < config.min_aroc:
            return False, [f"AROC_LOW_{aroc*100:.1f}%"]

    # 3. Downside Buffer Filter
    if config.filter_buffer:
        if buffer < config.min_buffer:
            return False, [f"BUFFER_LOW_{buffer*100:.1f}%"]

    # 4. IV Rank Filter
    if config.filter_ivr:
        ivr = candidate.iv_rank if candidate.iv_rank is not None else candidate.iv_percentile
        if ivr is None or ivr < config.min_ivr:
            return False, [f"IVR_BELOW_MIN"]

    # 5. POP Filter
    if config.filter_pop:
        if pop < config.min_pop:
            return False, [f"POP_LOW_{pop*100:.1f}%"]

    # 6. Earnings Risk Gate (DC-CSP-8)
    if config.filter_earnings:
        if candidate.earnings_status == EarningsStatus.EARNINGS_IMPACTED:
            return False, ["EARNINGS_WITHIN_DTE_WINDOW"]
        if config.strict_earnings and candidate.earnings_status == EarningsStatus.EARNINGS_UNVERIFIED:
            return False, ["EARNINGS_UNVERIFIED_STRICT"]

    # 7. Liquidity Gate (DC-CSP-6)
    if config.filter_liquidity:
        l_guard = evaluate_liquidity_guard(
            bid=candidate.bid,
            ask=candidate.ask,
            open_interest=candidate.open_interest,
            volume=candidate.volume
        )
        if l_guard.status == GuardStatus.REJECT:
            return False, l_guard.reasons

    return True, reasons


def rank_csp_boards(
    candidates: List[CSPCandidate],
    config: Optional[CSPFilterConfig] = None,
    alpha: float = 0.5,
    cash_pool: float = 50000.0
) -> Dict[str, List[RankedCSPItem]]:
    """
    Rank CSP candidates into 3 independent boards with in-memory execution.
    Adheres to DC-CSP-4, DC-CSP-6, DC-CSP-7, DC-CSP-8, DC-CSP-9.
    """
    cfg = config or CSPFilterConfig()

    board_harvest: List[RankedCSPItem] = []
    board_wheel: List[RankedCSPItem] = []
    board_vol_rank: List[RankedCSPItem] = []

    for c in candidates:
        # DC-CSP-3: Positive delta hard rejection
        # DC-CSP-6: Zero bid or crossed market hard reject
        # DC-CSP-4: DTE < 7 hard rejection
        if c.delta >= 0.0 or c.bid <= 0.0 or c.bid >= c.ask or c.dte < 7.0:
            continue

        try:
            p_exec = calculate_sell_pexec(c.bid, c.ask, alpha=alpha, bid_size=c.bid_size)
        except Exception:
            continue

        roc = calculate_roc(p_exec, c.strike)
        aroc = calculate_aroc(p_exec, c.strike, c.dte)
        buffer = calculate_downside_buffer(c.spot, c.strike)
        pop = calculate_pop(delta=c.delta)

        capital_info = calculate_csp_capital_allocation(
            cash_pool=cash_pool,
            strike=c.strike,
            pexec=p_exec,
            max_exposure_pct=cfg.max_underlying_exposure_pct
        )

        stress_pnl = calculate_stress_test_pnl(c.spot, c.strike, p_exec, drop_pct=0.15)

        # Apply user toggleable filters
        passes_filters, filter_reasons = evaluate_csp_filters(
            candidate=c,
            config=cfg,
            p_exec=p_exec,
            aroc=aroc,
            buffer=buffer,
            pop=pop,
            capital_info=capital_info
        )
        if not passes_filters:
            continue

        # Evaluate Board 1: Harvest
        res_harvest = evaluate_csp_harvest(c)
        if res_harvest.status != GuardStatus.REJECT:
            # Score primarily by AROC, with Gamma penalty if DTE < 21 (DC-CSP-4)
            gamma_factor = 0.85 if c.dte < 21.0 else 1.0
            score_harvest = aroc * 100.0 * gamma_factor
            board_harvest.append(RankedCSPItem(
                candidate=c,
                status=res_harvest.status,
                p_exec=round(p_exec, 2),
                roc=round(roc, 4),
                aroc=round(aroc, 4),
                buffer=round(buffer, 4),
                pop=round(pop, 4),
                capital_info=capital_info,
                stress_pnl=round(stress_pnl, 2),
                score=round(score_harvest, 2),
                reasons=res_harvest.reasons
            ))

        # Evaluate Board 2: Wheel / Dip-Buying
        res_wheel = evaluate_csp_wheel(c)
        if res_wheel.status != GuardStatus.REJECT:
            # Score by combination of Downside Buffer and Oversold Dip
            oversold_boost = max(0.0, (50.0 - c.rsi_14) / 50.0)
            score_wheel = (buffer * 100.0) + (oversold_boost * 20.0) + (roc * 50.0)
            board_wheel.append(RankedCSPItem(
                candidate=c,
                status=res_wheel.status,
                p_exec=round(p_exec, 2),
                roc=round(roc, 4),
                aroc=round(aroc, 4),
                buffer=round(buffer, 4),
                pop=round(pop, 4),
                capital_info=capital_info,
                stress_pnl=round(stress_pnl, 2),
                score=round(score_wheel, 2),
                reasons=res_wheel.reasons
            ))

        # Evaluate Board 3: High IV Rank Harvest
        res_vol = evaluate_csp_vol_rank(c)
        if res_vol.status != GuardStatus.REJECT:
            ivr = c.iv_rank if c.iv_rank is not None else (c.iv_percentile or 0.5)
            score_vol = (ivr * 100.0) + (aroc * 50.0)
            board_vol_rank.append(RankedCSPItem(
                candidate=c,
                status=res_vol.status,
                p_exec=round(p_exec, 2),
                roc=round(roc, 4),
                aroc=round(aroc, 4),
                buffer=round(buffer, 4),
                pop=round(pop, 4),
                capital_info=capital_info,
                stress_pnl=round(stress_pnl, 2),
                score=round(score_vol, 2),
                reasons=res_vol.reasons
            ))

    # Sort boards: PASS items first, then by score descending
    def sort_key(item: RankedCSPItem):
        status_rank = 0 if item.status == GuardStatus.PASS else 1
        return (status_rank, -item.score)

    board_harvest.sort(key=sort_key)
    board_wheel.sort(key=sort_key)
    board_vol_rank.sort(key=sort_key)

    return {
        "harvest": board_harvest,
        "wheel": board_wheel,
        "vol_rank": board_vol_rank,
    }
