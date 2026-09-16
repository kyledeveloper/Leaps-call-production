"""
Historical ATM IV data store and statistics calculator.
Computes IV Percentile, IV Rank, and IV Z-Score over rolling windows.
Adheres strictly to Defensive Clause 5 (minimum 90 days threshold, STRATEGY_DEGRADED fallback).
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.leaps_scanner.strategies.guards import GuardStatus


@dataclass(frozen=True)
class IVDataPoint:
    trade_date: str
    atm_iv: float


@dataclass(frozen=True)
class IVMetrics:
    symbol: str
    current_iv: float
    iv_percentile: float
    iv_rank: float
    iv_z_score: float
    valid_days: int
    coverage_tier: GuardStatus
    is_degraded: bool
    reasons: List[str] = field(default_factory=list)


class IVHistoryStore:
    """
    In-memory store for historical far-dated ATM implied volatility.
    """
    def __init__(self):
        self._store: Dict[str, List[IVDataPoint]] = {}

    def add_data_point(self, symbol: str, point: IVDataPoint) -> None:
        existing = self._store.setdefault(symbol, [])
        existing.append(point)
        # Sort and deduplicate by date
        seen_dates = set()
        deduped = []
        for p in sorted(existing, key=lambda x: x.trade_date):
            if p.trade_date not in seen_dates:
                seen_dates.add(p.trade_date)
                deduped.append(p)
        self._store[symbol] = deduped

    def get_metrics(self, symbol: str, current_iv: float, window: int = 252) -> IVMetrics:
        """
        Calculate IV percentile, rank, and z-score against historical ATM IVs.
        Enforces 90-day minimum valid history guard.
        """
        history = self._store.get(symbol, [])
        sub = history[-window:] if len(history) > window else history
        valid_days = len(sub)

        # Clause 5: If history < 90 days, degrade strategy unconditionally
        if valid_days < 90:
            return IVMetrics(
                symbol=symbol,
                current_iv=current_iv,
                iv_percentile=0.0,
                iv_rank=0.0,
                iv_z_score=0.0,
                valid_days=valid_days,
                coverage_tier=GuardStatus.REJECT,
                is_degraded=True,
                reasons=["STRATEGY_DEGRADED_INSUFFICIENT_HISTORY"]
            )

        # Coverage tier
        if valid_days >= 180:
            coverage_tier = GuardStatus.PASS
        else:
            coverage_tier = GuardStatus.WATCH

        iv_values = [p.atm_iv for p in sub]
        count_less = sum(1 for v in iv_values if v <= current_iv)
        iv_percentile = count_less / valid_days

        min_iv = min(iv_values)
        max_iv = max(iv_values)
        if max_iv - min_iv > 1e-6:
            iv_rank = (current_iv - min_iv) / (max_iv - min_iv)
        else:
            iv_rank = 0.5

        mean_iv = sum(iv_values) / valid_days
        var_iv = sum((v - mean_iv) ** 2 for v in iv_values) / (valid_days - 1) if valid_days > 1 else 0.0
        std_iv = math.sqrt(var_iv)

        if std_iv > 1e-6:
            iv_z_score = (current_iv - mean_iv) / std_iv
        else:
            iv_z_score = 0.0

        return IVMetrics(
            symbol=symbol,
            current_iv=current_iv,
            iv_percentile=iv_percentile,
            iv_rank=iv_rank,
            iv_z_score=iv_z_score,
            valid_days=valid_days,
            coverage_tier=coverage_tier,
            is_degraded=False,
            reasons=[]
        )
