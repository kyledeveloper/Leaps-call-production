"""
In-memory scoring and multi-board ranking module.
Re-ranks cached option opportunities instantly across 3 independent strategy boards
when the execution alpha slider changes.
Adheres strictly to Global Invariant 1 (P_exec) and Global Invariant 6 (Zero network re-fetch).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.leaps_scanner.core.metrics import (
    calculate_pexec,
    calculate_carry_cost,
    calculate_effective_leverage,
    calculate_round_trip
)
from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
from src.leaps_scanner.strategies.vol_discount import (
    evaluate_vol_discount_underlying,
    evaluate_vol_discount_contract,
    VolDiscountUnderlyingMetrics
)
from src.leaps_scanner.strategies.oversold import (
    evaluate_oversold_underlying,
    evaluate_oversold_contract,
    OversoldUnderlyingMetrics
)
from src.leaps_scanner.strategies.guards import GuardStatus
from src.leaps_scanner.data.universe import SymbologyNormalizer
from src.leaps_scanner.data.funnel import keep_scan_delta


@dataclass
class StrategyCandidate:
    symbol: str
    underlying: str
    strike: float
    spot: float
    dte: float
    bid: float
    ask: float
    delta: float
    open_interest: int
    volume: int
    strategy_name: str = "deep_itm"
    dividend_yield: float = 0.0
    ask_size: int = 10
    iv: Optional[float] = None
    is_etf: bool = False
    pct_change_20d: float = 0.0
    drawdown_52w_high: float = 0.0
    bounce_52w_low: float = 0.0
    rsi_14: float = 50.0
    pct_to_200dma: float = 0.0
    hv_252: float = 0.25
    hv_20: float = 0.25
    iv_percentile: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_z_score: Optional[float] = None
    valid_history_days: int = 252
    iv_history_days: int = 0
    hv_percentile: Optional[float] = None
    hv_z_score: float = 0.0
    days_to_earnings: Optional[int] = None
    liquidity_status: GuardStatus = GuardStatus.PASS
    data_quality: str = "REALTIME"


@dataclass(frozen=True)
class RankedItem:
    symbol: str
    underlying: str
    strike: float
    spot: float
    dte: float
    bid: float
    ask: float
    p_exec: float
    p_sell: float
    round_trip_per_contract: float
    delta: float
    intrinsic_per_share: float
    extrinsic_per_share: float
    carry_cost: float
    effective_leverage: float
    status: GuardStatus
    reasons: List[str]
    open_interest: int
    volume: int
    strategy_name: str
    vol_oi_ratio: float = 0.0
    dollar_volume: float = 0.0
    confluence_score: float = 0.0
    iv_percentile: Optional[float] = None
    regime: str = "NONE"
    theta_daily_pct: float = 0.0


class MemoryRanker:
    """
    Maintains active snapshot in memory and recalculates multi-board rankings on demand.
    """
    def __init__(self, candidates: List[StrategyCandidate]):
        self._candidates: List[StrategyCandidate] = []
        for c in candidates:
            c.underlying = SymbologyNormalizer.to_canonical(c.underlying)
            if not keep_scan_delta(c.delta):
                continue
            self._candidates.append(c)

    def rank(self, alpha: float = 0.5) -> List[RankedItem]:
        """Backward-compatible default rank for primary strategy (deep_itm)."""
        boards = self.rank_boards(alpha=alpha)
        return boards.get("deep_itm", [])

    def rank_boards(self, alpha: float = 0.5) -> Dict[str, List[RankedItem]]:
        """
        Instant in-memory re-ranking returning 3 separate strategy leaderboards.
        """
        tier_order = {GuardStatus.PASS: 0, GuardStatus.WATCH: 1, GuardStatus.REJECT: 2}

        deep_itm_items: List[RankedItem] = []
        vol_discount_items: List[RankedItem] = []
        oversold_items: List[RankedItem] = []

        for c in self._candidates:
            pexec_res = calculate_pexec(
                bid=c.bid,
                ask=c.ask,
                ask_size=c.ask_size,
                alpha=alpha,
                target_contracts=5
            )
            rt_res = calculate_round_trip(
                bid=c.bid,
                ask=c.ask,
                alpha=alpha,
                alpha_exit=alpha
            )
            carry_res = calculate_carry_cost(
                spot=c.spot,
                strike=c.strike,
                dte=c.dte,
                p_exec=pexec_res.p_exec,
                dividend_yield=c.dividend_yield
            )
            leverage = calculate_effective_leverage(delta=c.delta, spot=c.spot, p_exec=pexec_res.p_exec)

            # 1. Strategy 1: Deep ITM
            s1_res = evaluate_deep_itm(
                spot=c.spot,
                strike=c.strike,
                dte=c.dte,
                p_exec=pexec_res.p_exec,
                delta=c.delta,
                dividend_yield=c.dividend_yield,
                iv=c.iv,
                bid=c.bid,
                ask=c.ask,
                open_interest=c.open_interest,
                volume=c.volume,
                ask_size=c.ask_size,
            )
            deep_itm_items.append(RankedItem(
                symbol=c.symbol,
                underlying=c.underlying,
                strike=c.strike,
                spot=c.spot,
                dte=c.dte,
                bid=c.bid,
                ask=c.ask,
                p_exec=pexec_res.p_exec,
                p_sell=rt_res.p_sell,
                round_trip_per_contract=rt_res.round_trip_per_contract,
                delta=c.delta,
                intrinsic_per_share=carry_res.intrinsic_per_share,
                extrinsic_per_share=carry_res.extrinsic_per_share,
                carry_cost=s1_res.carry_cost,
                effective_leverage=s1_res.effective_leverage,
                status=s1_res.status,
                reasons=s1_res.reasons,
                open_interest=c.open_interest,
                volume=c.volume,
                strategy_name="deep_itm",
                theta_daily_pct=s1_res.theta_daily_pct,
            ))

            # 2. Strategy 2: Volatility Discount (IV warehouse or HV proxy)
            use_iv_path = c.iv is not None and c.iv_percentile is not None and (
                c.iv_history_days >= 90 or (c.iv_history_days == 0 and c.valid_history_days >= 90)
            )
            use_hv_path = c.hv_percentile is not None and c.hv_252 > 0
            if use_iv_path or use_hv_path:
                vol_metrics = VolDiscountUnderlyingMetrics(
                    symbol=c.underlying,
                    spot=c.spot,
                    pct_change_20d=c.pct_change_20d,
                    drawdown_52w_high=c.drawdown_52w_high,
                    current_atm_iv=c.iv if c.iv is not None else 0.0,
                    hv_252=c.hv_252,
                    iv_percentile=c.iv_percentile if c.iv_percentile is not None else 0.0,
                    iv_rank=c.iv_rank if c.iv_rank is not None else 0.5,
                    iv_z_score=c.iv_z_score if c.iv_z_score is not None else 0.0,
                    valid_history_days=c.iv_history_days if c.iv_history_days > 0 else c.valid_history_days,
                    is_degraded=not use_iv_path,
                    hv_20=c.hv_20,
                    hv_percentile=c.hv_percentile,
                    hv_z_score=c.hv_z_score,
                )
                vol_u_res = evaluate_vol_discount_underlying(vol_metrics)
                vol_c_res = evaluate_vol_discount_contract(
                    underlying_result=vol_u_res,
                    spot=c.spot,
                    strike=c.strike,
                    dte=c.dte,
                    p_exec=pexec_res.p_exec,
                    delta=c.delta,
                    liquidity_status=c.liquidity_status,
                    dividend_yield=c.dividend_yield,
                    days_to_earnings=c.days_to_earnings
                )
                vol_discount_items.append(RankedItem(
                    symbol=c.symbol,
                    underlying=c.underlying,
                    strike=c.strike,
                    spot=c.spot,
                    dte=c.dte,
                    bid=c.bid,
                    ask=c.ask,
                    p_exec=pexec_res.p_exec,
                    p_sell=rt_res.p_sell,
                    round_trip_per_contract=rt_res.round_trip_per_contract,
                    delta=c.delta,
                    intrinsic_per_share=carry_res.intrinsic_per_share,
                    extrinsic_per_share=carry_res.extrinsic_per_share,
                    carry_cost=vol_c_res.carry_cost,
                    effective_leverage=vol_c_res.effective_leverage,
                    status=vol_c_res.status,
                    reasons=vol_c_res.reasons,
                    open_interest=c.open_interest,
                    volume=c.volume,
                    strategy_name="vol_discount",
                    iv_percentile=vol_u_res.iv_percentile,
                    regime=vol_c_res.regime
                ))
            else:
                reject_reasons = []
                if c.iv is None:
                    reject_reasons.append("ZERO_EXTRINSIC_IV_UNAVAILABLE")
                if c.iv_percentile is None:
                    reject_reasons.append("MISSING_IV_PERCENTILE")
                if c.hv_percentile is None:
                    reject_reasons.append("MISSING_HV_PROXY")
                vol_discount_items.append(RankedItem(
                    symbol=c.symbol,
                    underlying=c.underlying,
                    strike=c.strike,
                    spot=c.spot,
                    dte=c.dte,
                    bid=c.bid,
                    ask=c.ask,
                    p_exec=pexec_res.p_exec,
                    p_sell=rt_res.p_sell,
                    round_trip_per_contract=rt_res.round_trip_per_contract,
                    delta=c.delta,
                    intrinsic_per_share=carry_res.intrinsic_per_share,
                    extrinsic_per_share=carry_res.extrinsic_per_share,
                    carry_cost=carry_res.total_annualized_carry,
                    effective_leverage=leverage,
                    status=GuardStatus.REJECT,
                    reasons=reject_reasons,
                    open_interest=c.open_interest,
                    volume=c.volume,
                    strategy_name="vol_discount",
                    iv_percentile=c.iv_percentile,
                    regime="NONE"
                ))

            # 3. Strategy 3: Blue-Chip Oversold Confluence
            oversold_u_metrics = OversoldUnderlyingMetrics(
                symbol=c.underlying,
                spot=c.spot,
                rsi_14=c.rsi_14,
                pct_to_200dma=c.pct_to_200dma,
                drawdown_52w_high=c.drawdown_52w_high,
                bounce_52w_low=c.bounce_52w_low,
                hv_20=c.hv_20,
                bar_count=c.valid_history_days,
                is_valid=c.valid_history_days >= 200
            )
            oversold_u_res = evaluate_oversold_underlying(oversold_u_metrics)
            oversold_c_res = evaluate_oversold_contract(
                underlying_result=oversold_u_res,
                spot=c.spot,
                strike=c.strike,
                dte=c.dte,
                p_exec=pexec_res.p_exec,
                delta=c.delta,
                liquidity_status=c.liquidity_status,
                dividend_yield=c.dividend_yield,
                days_to_earnings=c.days_to_earnings
            )
            oversold_items.append(RankedItem(
                symbol=c.symbol,
                underlying=c.underlying,
                strike=c.strike,
                spot=c.spot,
                dte=c.dte,
                bid=c.bid,
                ask=c.ask,
                p_exec=pexec_res.p_exec,
                p_sell=rt_res.p_sell,
                round_trip_per_contract=rt_res.round_trip_per_contract,
                delta=c.delta,
                intrinsic_per_share=carry_res.intrinsic_per_share,
                extrinsic_per_share=carry_res.extrinsic_per_share,
                carry_cost=oversold_c_res.carry_cost,
                effective_leverage=oversold_c_res.effective_leverage,
                status=oversold_c_res.status,
                reasons=oversold_c_res.reasons,
                open_interest=c.open_interest,
                volume=c.volume,
                strategy_name="oversold",
                confluence_score=oversold_c_res.confluence_score
            ))

        deep_itm_items.sort(key=lambda x: (tier_order.get(x.status, 3), x.carry_cost))
        vol_discount_items.sort(key=lambda x: (tier_order.get(x.status, 3), x.iv_percentile if x.iv_percentile is not None else 1.0))
        oversold_items.sort(key=lambda x: (tier_order.get(x.status, 3), -x.confluence_score, x.carry_cost))

        return {
            "deep_itm": deep_itm_items,
            "vol_discount": vol_discount_items,
            "oversold": oversold_items,
        }
