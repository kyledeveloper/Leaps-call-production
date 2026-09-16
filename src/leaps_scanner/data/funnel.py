"""
Multi-stage funnel filtering module.
Implements Level 1 (Universe), Level 2 (DTE >= 250), and Level 3 (Strategy-Aware Strike Slicing).
Strictly adheres to Defensive Clause 3.
"""
from typing import Any, Dict, List, Sequence, Tuple

# Strike price ratios relative to underlying spot price S
STRATEGY_STRIKE_RATIOS: Dict[str, Tuple[float, float]] = {
    "deep_itm": (0.65, 0.85),       # Stock-replacement zone; 0.50S is prepaid equity
    "vol_discount": (0.70, 1.25),   # Long vega ATM / near-money contracts
    "oversold": (0.50, 1.25),       # Mean-reversion ITM/ATM LEAPS
    "unusual_flow": (0.70, 1.35)    # Far-dated institutional flow
}

DEFAULT_RATIO_RANGE = (0.50, 1.35)
# Calls with delta above this are prepaid stock — skip ingest, ranking, and boards.
MAX_CALL_DELTA = 0.90


def keep_scan_delta(delta: float) -> bool:
    """False when the contract is prepaid equity (delta > 0.90)."""
    try:
        return float(delta) <= MAX_CALL_DELTA
    except (TypeError, ValueError):
        return True


def filter_expirations(
    expirations: Sequence[Dict[str, Any]],
    min_dte: float = 250.0
) -> List[Dict[str, Any]]:
    """
    Level 2 Funnel: Discard non-LEAPS expirations with DTE < min_dte.
    """
    return [e for e in expirations if float(e.get("dte", 0.0)) >= min_dte]


def get_strike_window(
    spot: float,
    active_strategies: Sequence[str]
) -> Tuple[float, float]:
    """
    Level 3 Funnel: Compute union of strike price windows across active strategies.
    Ensures Deep ITM contracts with Delta ~ 0.85 are never clipped.
    """
    if not active_strategies:
        return (DEFAULT_RATIO_RANGE[0] * spot, DEFAULT_RATIO_RANGE[1] * spot)

    min_ratio = 1.0
    max_ratio = 1.0
    found_any = False

    for strat in active_strategies:
        strat_key = strat.lower().strip()
        if strat_key in STRATEGY_STRIKE_RATIOS:
            low, high = STRATEGY_STRIKE_RATIOS[strat_key]
            if not found_any:
                min_ratio, max_ratio = low, high
                found_any = True
            else:
                min_ratio = min(min_ratio, low)
                max_ratio = max(max_ratio, high)

    if not found_any:
        min_ratio, max_ratio = DEFAULT_RATIO_RANGE

    return (round(min_ratio * spot, 4), round(max_ratio * spot, 4))


def filter_strikes(
    strikes: Sequence[float],
    spot: float,
    active_strategies: Sequence[str]
) -> List[float]:
    """
    Level 3 Funnel: Retain only strikes falling into the strategy-aware window.
    """
    low_bound, high_bound = get_strike_window(spot, active_strategies)
    return [k for k in strikes if low_bound <= k <= high_bound]
