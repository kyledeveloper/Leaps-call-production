"""
Quantitative and financial metrics module.
Provides conservative execution price (P_exec), slippage, carry cost, and effective leverage.
Adheres strictly to the 6 Global Invariants.
"""
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

    total_carry = extrinsic_rate + dividend_yield

    return CarryCostResult(
        intrinsic_per_share=intrinsic,
        extrinsic_per_share=extrinsic,
        annualized_extrinsic_rate=extrinsic_rate,
        total_annualized_carry=total_carry,
        dividend_yield=dividend_yield,
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
