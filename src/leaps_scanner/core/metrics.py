"""
Quantitative and financial metrics module.
Provides conservative execution price (P_exec), slippage, carry cost, and effective leverage.
Adheres strictly to the 6 Global Invariants.
"""
import math
from dataclasses import dataclass
from typing import Optional

YEAR_DAYS: float = 365.25
CONTRACT_MULTIPLIER: int = 100


@dataclass(frozen=True)
class PExecResult:
    p_exec: float
    mid: float
    half_spread: float
    effective_alpha: float
    alpha_elevated: bool


@dataclass(frozen=True)
class RoundTripResult:
    p_exec: float
    p_sell: float
    round_trip_per_share: float
    round_trip_per_contract: float


@dataclass(frozen=True)
class CarryCostResult:
    intrinsic_per_share: float
    extrinsic_per_share: float
    annualized_extrinsic_rate: float
    total_annualized_carry: float
    dividend_yield: float
    t_years: float


def calculate_pexec(
    bid: float,
    ask: float,
    ask_size: int = 10,
    alpha: float = 0.5,
    target_contracts: int = 5
) -> PExecResult:
    """
    Calculate conservative execution price P_exec = Mid + alpha * half_spread.
    Elevates alpha to at least 0.75 if displayed ask_size < target_contracts.
    """
    if bid < 0 or ask <= 0 or bid > ask:
        raise ValueError(f"Invalid quote prices: bid={bid}, ask={ask}")

    mid = (bid + ask) / 2.0
    half_spread = (ask - bid) / 2.0

    effective_alpha = float(alpha)
    alpha_elevated = False

    if ask_size < target_contracts:
        effective_alpha = max(effective_alpha, 0.75)
        alpha_elevated = True

    p_exec = mid + effective_alpha * half_spread
    return PExecResult(
        p_exec=p_exec,
        mid=mid,
        half_spread=half_spread,
        effective_alpha=effective_alpha,
        alpha_elevated=alpha_elevated
    )


def calculate_round_trip(
    bid: float,
    ask: float,
    alpha: float = 0.5,
    alpha_exit: Optional[float] = None
) -> RoundTripResult:
    """
    Calculate round-trip execution cost (entry at P_exec, exit at P_sell).
    """
    mid = (bid + ask) / 2.0
    half_spread = (ask - bid) / 2.0

    if alpha_exit is None:
        alpha_exit = alpha

    p_exec = mid + alpha * half_spread
    p_sell = mid - alpha_exit * half_spread

    rt_per_share = p_exec - p_sell
    rt_per_contract = rt_per_share * CONTRACT_MULTIPLIER

    return RoundTripResult(
        p_exec=p_exec,
        p_sell=p_sell,
        round_trip_per_share=rt_per_share,
        round_trip_per_contract=rt_per_contract
    )


def calculate_carry_cost(
    spot: float,
    strike: float,
    dte: float,
    p_exec: float,
    dividend_yield: float = 0.0
) -> CarryCostResult:
    """
    Calculate comprehensive annualized carry cost:
    Carry = Extrinsic / (P_exec * T) + q_div
    """
    if dividend_yield is None or (isinstance(dividend_yield, float) and math.isnan(dividend_yield)):
        q_clean = 0.0
    else:
        try:
            q_clean = max(0.0, float(dividend_yield))
        except (TypeError, ValueError):
            q_clean = 0.0

    if dte <= 0:
        t_years = 0.0
    else:
        t_years = dte / YEAR_DAYS

    intrinsic = max(0.0, spot - strike)
    extrinsic = max(0.0, p_exec - intrinsic)

    if p_exec > 0.0 and t_years > 0.0:
        extrinsic_rate = extrinsic / (p_exec * t_years)
    else:
        extrinsic_rate = 0.0

    total_carry = extrinsic_rate + q_clean

    return CarryCostResult(
        intrinsic_per_share=intrinsic,
        extrinsic_per_share=extrinsic,
        annualized_extrinsic_rate=extrinsic_rate,
        total_annualized_carry=total_carry,
        dividend_yield=q_clean,
        t_years=t_years
    )


def calculate_effective_leverage(
    delta: float,
    spot: float,
    p_exec: float
) -> float:
    """
    Calculate effective leverage: Delta * Spot / P_exec.
    """
    if p_exec <= 0.0:
        return 0.0
    return (delta * spot) / p_exec


def calculate_sell_pexec(
    bid: float,
    ask: float,
    alpha: float = 0.5,
    bid_size: int = 10,
    target_contracts: int = 1
) -> float:
    """
    DC-CSP-6: Conservative sell-side execution price:
    P_exec = Mid - alpha * (Mid - Bid)
    Elevates alpha to at least 0.85 if bid_size < target_contracts.
    Strictly guarantees Bid <= P_exec <= Mid.
    """
    if bid <= 0.0 or ask <= 0.0 or bid >= ask:
        raise ValueError(f"Invalid sell quote: bid={bid}, ask={ask}")

    mid = (bid + ask) / 2.0
    half_spread = (ask - bid) / 2.0

    effective_alpha = float(alpha)
    if bid_size < target_contracts:
        effective_alpha = max(effective_alpha, 0.85)

    p_exec = mid - effective_alpha * half_spread
    return max(bid, min(mid, p_exec))


def calculate_roc(pexec: float, strike: float) -> float:
    """Single period Return on Capital (ROC) = P_exec / Strike."""
    if strike <= 0.0:
        return 0.0
    return max(0.0, pexec / strike)


def calculate_aroc(pexec: float, strike: float, dte: float) -> float:
    """
    DC-CSP-4: Annualized Return on Capital (AROC) = ROC * (365 / dte_safe).
    Floors DTE at 7.0 to eliminate short-DTE division-by-zero or astronomical distortion.
    """
    if strike <= 0.0:
        return 0.0
    dte_safe = max(float(dte), 7.0)
    roc = calculate_roc(pexec, strike)
    return roc * (365.0 / dte_safe)


def calculate_downside_buffer(spot: float, strike: float) -> float:
    """Downside Buffer = (Spot - Strike) / Spot."""
    if spot <= 0.0:
        return 0.0
    return (spot - strike) / spot


def calculate_pop(
    delta: Optional[float] = None,
    spot: Optional[float] = None,
    breakeven: Optional[float] = None,
    dte: Optional[float] = None,
    r: float = 0.04,
    q: float = 0.01,
    sigma: float = 0.25
) -> float:
    """
    DC-CSP-5: Probability of Profit for a short put.
    Prefer the lognormal probability that spot expires above break-even.
    Fall back to 1-|Delta| (approx. OTM probability) when BE inputs are missing.
    """
    if (
        spot is not None and breakeven is not None and dte is not None
        and spot > 0.0 and breakeven > 0.0 and dte > 0.0 and sigma > 0.0
    ):
        t = dte / 365.25
        d2 = (math.log(spot / breakeven) + (r - q - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
        from src.leaps_scanner.core.american_pricing import _cnd
        return max(0.0, min(1.0, _cnd(d2)))

    if delta is not None:
        return max(0.0, min(1.0, 1.0 - abs(float(delta))))

    return 0.50


def calculate_csp_capital_allocation(
    cash_pool: float,
    strike: float,
    pexec: float,
    max_exposure_pct: float = 0.25
) -> dict:
    """
    DC-CSP-7: Concentration-capped contract allocation and Max Loss.
    """
    capital_per_contract = max(0.0, strike * CONTRACT_MULTIPLIER)
    if capital_per_contract <= 0.0 or cash_pool <= 0.0:
        return {
            "required_capital_per_contract": capital_per_contract,
            "recommended_contracts": 0,
            "max_loss_per_contract": 0.0,
            "total_max_loss": 0.0,
            "total_premium": 0.0,
        }

    max_ticker_capital = cash_pool * max(0.01, min(1.0, float(max_exposure_pct)))
    contracts_by_exposure = int(max_ticker_capital // capital_per_contract)
    contracts_by_total = int(cash_pool // capital_per_contract)
    recommended = max(0, min(contracts_by_exposure, contracts_by_total))

    max_loss_per_share = max(0.0, strike - pexec)
    max_loss_per_contract = max_loss_per_share * CONTRACT_MULTIPLIER
    total_max_loss = recommended * max_loss_per_contract
    total_premium = recommended * pexec * CONTRACT_MULTIPLIER

    return {
        "required_capital_per_contract": capital_per_contract,
        "recommended_contracts": recommended,
        "max_loss_per_contract": round(max_loss_per_contract, 2),
        "total_max_loss": round(total_max_loss, 2),
        "total_premium": round(total_premium, 2),
    }


def calculate_stress_test_pnl(
    spot: float,
    strike: float,
    pexec: float,
    drop_pct: float = 0.15
) -> float:
    """
    DC-CSP-5: Stress test PnL per contract for underlying single-day jump drop (default -15%).
    Stress PnL = 100 * (P_exec - max(0, Strike - Spot * (1 - drop_pct)))
    """
    stressed_spot = spot * (1.0 - drop_pct)
    stressed_intrinsic = max(0.0, strike - stressed_spot)
    pnl_per_share = pexec - stressed_intrinsic
    return pnl_per_share * CONTRACT_MULTIPLIER

