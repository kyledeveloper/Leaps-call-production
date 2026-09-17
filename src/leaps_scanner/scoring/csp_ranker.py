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
from src.leaps_scanner.strategies.guards import GuardStatus, evaluate_liquidity_guard, fold_gates


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
    gates: Dict[str, str] = field(default_factory=dict)


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
    capital_info: dict,
    board: str = "harvest",
) -> tuple[bool, List[str]]:
    """
    Check candidate against user toggleable filters.
    Harvest owns AROC / Buffer / POP hard gates.
    Wheel evaluates buffer & liquidity.
    Vol-rank owns IVR and earnings gates.
    """
    reasons = []
    key = (board or "harvest").replace("csp_", "")

    if config.max_capital_per_contract is not None and config.max_capital_per_contract > 0:
        if capital_info["required_capital_per_contract"] > config.max_capital_per_contract:
            return False, ["EXCEEDS_MAX_CAPITAL_PER_CONTRACT"]

    if config.filter_aroc and key in ("harvest", "vol_rank"):
        threshold = 0.18 if key == "vol_rank" else config.min_aroc
        if aroc < threshold:
            return False, [f"AROC_LOW_{aroc*100:.1f}%"]

    if config.filter_buffer and key in ("harvest", "wheel"):
        if buffer < config.min_buffer:
            return False, [f"BUFFER_LOW_{buffer*100:.1f}%"]

    if config.filter_ivr and key == "vol_rank":
        ivr = candidate.iv_rank if candidate.iv_rank is not None else candidate.iv_percentile
        if ivr is None or ivr < config.min_ivr:
            return False, ["IVR_BELOW_MIN"]

    if config.filter_pop and key == "harvest":
        if pop < config.min_pop:
            return False, [f"POP_LOW_{pop*100:.1f}%"]

    if config.filter_earnings:
        if candidate.earnings_status == EarningsStatus.EARNINGS_IMPACTED:
            return False, ["EARNINGS_WITHIN_DTE_WINDOW"]
        if config.strict_earnings and candidate.earnings_status == EarningsStatus.EARNINGS_UNVERIFIED:
            return False, ["EARNINGS_UNVERIFIED_STRICT"]

    if config.filter_liquidity:
        l_guard = evaluate_liquidity_guard(
            bid=candidate.bid,
            ask=candidate.ask,
            open_interest=candidate.open_interest,
            volume=candidate.volume,
            bid_size=candidate.bid_size,
            ask_size=candidate.ask_size,
        )
        # OTM puts often print OI without daily volume. Do not hard-drop on volume alone.
        if (
            l_guard.spread_status == GuardStatus.REJECT
            or l_guard.oi_status == GuardStatus.REJECT
        ):
            return False, list(l_guard.reasons)
        if l_guard.volume_status == GuardStatus.REJECT:
            reasons.append("VOLUME_THIN")

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
        breakeven = c.strike - p_exec
        pop = calculate_pop(
            delta=c.delta,
            spot=c.spot,
            breakeven=breakeven,
            dte=c.dte,
            sigma=float(c.iv) if c.iv else 0.25,
        )

        capital_info = calculate_csp_capital_allocation(
            cash_pool=cash_pool,
            strike=c.strike,
            pexec=p_exec,
            max_exposure_pct=cfg.max_underlying_exposure_pct
        )

        stress_pnl = calculate_stress_test_pnl(c.spot, c.strike, p_exec, drop_pct=0.15)

        def emit(
            bucket: List[RankedCSPItem],
            res,
            board_key: str,
            score: float,
        ) -> None:
            # Build full dictionary of gates for this candidate on this board
            gates: Dict[str, str] = {
                k: (v.value if hasattr(v, "value") else str(v))
                for k, v in getattr(res, "gates", {}).items()
            }
            if board_key in ("harvest", "vol_rank"):
                min_a = 0.25 if board_key == "vol_rank" else 0.15
                watch_a = 0.18 if board_key == "vol_rank" else 0.12
                if aroc >= min_a:
                    gates["aroc"] = GuardStatus.PASS.value
                elif aroc >= watch_a:
                    gates["aroc"] = GuardStatus.WATCH.value
                else:
                    gates["aroc"] = GuardStatus.REJECT.value

            if board_key == "harvest":
                if pop >= 0.75:
                    gates["pop"] = GuardStatus.PASS.value
                elif pop >= 0.70:
                    gates["pop"] = GuardStatus.WATCH.value
                else:
                    gates["pop"] = GuardStatus.REJECT.value

            passes_filters, filter_reasons = evaluate_csp_filters(
                candidate=c,
                config=cfg,
                p_exec=p_exec,
                aroc=aroc,
                buffer=buffer,
                pop=pop,
                capital_info=capital_info,
                board=board_key,
            )
            # Baseline status comes from folding all gates
            gate_enum_map = {k: GuardStatus(v) for k, v in gates.items() if v in GuardStatus.__members__}
            base_status = fold_gates(gate_enum_map) if gate_enum_map else res.status
            status = base_status if passes_filters else GuardStatus.REJECT
            reasons = list(res.reasons)
            if not passes_filters:
                reasons = list(filter_reasons) + reasons

            bucket.append(RankedCSPItem(
                candidate=c,
                status=status,
                p_exec=round(p_exec, 2),
                roc=round(roc, 4),
                aroc=round(aroc, 4),
                buffer=round(buffer, 4),
                pop=round(pop, 4),
                capital_info=capital_info,
                stress_pnl=round(stress_pnl, 2),
                score=round(score, 2),
                reasons=reasons,
                gates=gates,
            ))

        res_harvest = evaluate_csp_harvest(c)
        gamma_factor = 0.85 if c.dte < 21.0 else 1.0
        emit(board_harvest, res_harvest, "harvest", aroc * 100.0 * gamma_factor)

        res_wheel = evaluate_csp_wheel(c)
        oversold_boost = max(0.0, (50.0 - c.rsi_14) / 50.0)
        emit(board_wheel, res_wheel, "wheel", (buffer * 100.0) + (oversold_boost * 20.0) + (roc * 50.0))

        res_vol = evaluate_csp_vol_rank(c)
        ivr = c.iv_rank if c.iv_rank is not None else (c.iv_percentile or 0.5)
        emit(board_vol_rank, res_vol, "vol_rank", (ivr * 100.0) + (aroc * 50.0))

    # Sort boards: PASS items first, then by score descending
    def sort_key(item: RankedCSPItem):
        if item.status == GuardStatus.PASS:
            status_rank = 0
        elif item.status == GuardStatus.WATCH:
            status_rank = 1
        else:
            status_rank = 2
        return (status_rank, -item.score)

    board_harvest.sort(key=sort_key)
    board_wheel.sort(key=sort_key)
    board_vol_rank.sort(key=sort_key)

    return {
        "harvest": board_harvest,
        "wheel": board_wheel,
        "vol_rank": board_vol_rank,
    }
