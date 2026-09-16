"""
In-memory scoring and multi-board ranking module.
Re-ranks cached option opportunities instantly across 4 independent strategy boards
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
from src.leaps_scanner.strategies.unusual_flow import (
    evaluate_unusual_flow,
    UnusualFlowInput
)
from src.leaps_scanner.strategies.guards import GuardStatus
from src.leaps_scanner.data.universe import SymbologyNormalizer


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
    days_to_earnings: Optional[int] = None
    liquidity_status: GuardStatus = GuardStatus.PASS


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


class MemoryRanker:
    """
    Maintains active snapshot in memory and recalculates multi-board rankings on demand.
    """
    def __init__(self, candidates: List[StrategyCandidate]):
        self._candidates: List[StrategyCandidate] = []
        for c in candidates:
            # Defensive normalization: ensure candidate underlying is canonical
            c.underlying = SymbologyNormalizer.to_canonical(c.underlying)
            self._candidates.append(c)

    def rank(self, alpha: float = 0.5) -> List[RankedItem]:
        """Backward-compatible default rank for primary strategy (deep_itm)."""
        boards = self.rank_boards(alpha=alpha)
        return boards.get("deep_itm", [])

    def rank_boards(self, alpha: float = 0.5) -> Dict[str, List[RankedItem]]:
        """
        Instant in-memory re-ranking returning 4 separate strategy leaderboards.
        """
        tier_order = {GuardStatus.PASS: 0, GuardStatus.WATCH: 1, GuardStatus.REJECT: 2}

        deep_itm_items: List[RankedItem] = []
        vol_discount_items: List[RankedItem] = []
        oversold_items: List[RankedItem] = []
        unusual_flow_items: List[RankedItem] = []

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
                iv=c.iv
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
                carry_cost=carry_res.total_annualized_carry,
                effective_leverage=s1_res.effective_leverage,
                status=s1_res.status,
                reasons=s1_res.reasons,
                open_interest=c.open_interest,
                volume=c.volume,
                strategy_name="deep_itm"
            ))

            # 2. Strategy 2: Volatility Discount
            if c.iv_percentile is not None and c.iv is not None:
                vol_metrics = VolDiscountUnderlyingMetrics(
                    symbol=c.underlying,
                    spot=c.spot,
                    pct_change_20d=c.pct_change_20d,
                    drawdown_52w_high=c.drawdown_52w_high,
                    current_atm_iv=c.iv,
                    hv_252=c.hv_252,
                    iv_percentile=c.iv_percentile,
                    iv_rank=c.iv_rank if c.iv_rank is not None else 0.5,
                    iv_z_score=c.iv_z_score if c.iv_z_score is not None else 0.0,
                    valid_history_days=c.valid_history_days,
                    is_degraded=c.valid_history_days < 90
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
                    iv_percentile=c.iv_percentile,
                    regime=vol_c_res.regime
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

            # 4. Strategy 4: Unusual Flow
            flow_inp = UnusualFlowInput(
                symbol=c.symbol,
                underlying=c.underlying,
                spot=c.spot,
                strike=c.strike,
                dte=c.dte,
                bid=c.bid,
                ask=c.ask,
                volume=c.volume,
                open_interest=c.open_interest,
                is_etf=c.is_etf,
                liquidity_status=c.liquidity_status
            )
            flow_res = evaluate_unusual_flow(flow_inp)
            unusual_flow_items.append(RankedItem(
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
                status=flow_res.status,
                reasons=flow_res.reasons,
                open_interest=c.open_interest,
                volume=c.volume,
                strategy_name="unusual_flow",
                vol_oi_ratio=flow_res.vol_oi_ratio,
                dollar_volume=flow_res.dollar_volume
            ))

        # Sort Boards
        # 1. Deep ITM: Tier, then lowest carry cost
        deep_itm_items.sort(key=lambda x: (tier_order.get(x.status, 3), x.carry_cost))

        # 2. Vol Discount: Tier, then lowest IV percentile
        vol_discount_items.sort(key=lambda x: (tier_order.get(x.status, 3), x.iv_percentile if x.iv_percentile is not None else 1.0))

        # 3. Oversold: Tier, then highest confluence score, then lowest carry cost
        oversold_items.sort(key=lambda x: (tier_order.get(x.status, 3), -x.confluence_score, x.carry_cost))

        # 4. Unusual Flow: Tier, then highest dollar volume, then highest vol/oi
        unusual_flow_items.sort(key=lambda x: (tier_order.get(x.status, 3), -x.dollar_volume, -x.vol_oi_ratio))

        return {
            "deep_itm": deep_itm_items,
            "vol_discount": vol_discount_items,
            "oversold": oversold_items,
            "unusual_flow": unusual_flow_items
        }
