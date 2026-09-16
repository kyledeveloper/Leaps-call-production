"""
Daily price history store and technical indicators calculation.
Hermetic, pure Python implementation for RSI14, 200DMA, HV252, HV20, and 52-week High/Low.
Adheres strictly to Defensive Clause 6 (minimum 200 daily bars guard).
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PriceBar:
    trade_date: str
    close: float
    high: float
    low: float
    volume: float
    open: Optional[float] = None


@dataclass(frozen=True)
class PriceMetrics:
    symbol: str
    spot: float
    rsi_14: float
    sma_200: float
    pct_to_200dma: float
    high_52w: float
    drawdown_52w_high: float
    low_52w: float
    bounce_52w_low: float
    hv_252: float
    hv_20: float
    bar_count: int
    is_valid: bool
    pct_change_20d: float = 0.0
    hv_percentile: Optional[float] = None
    hv_z_score: float = 0.0
    reasons: List[str] = field(default_factory=list)


def calculate_sma(prices: List[float], period: int) -> float:
    """Calculate Simple Moving Average over the last `period` elements."""
    if not prices or period <= 0:
        return 0.0
    sub = prices[-period:]
    return sum(sub) / len(sub)


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculate Wilder's Relative Strength Index (RSI).
    """
    if len(prices) < 2:
        return 50.0

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    if not deltas or all(abs(d) < 1e-9 for d in deltas):
        return 50.0

    gains = [max(0.0, d) for d in deltas]
    losses = [max(0.0, -d) for d in deltas]

    if len(deltas) < period:
        # Fewer bars than period: use simple average
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
    else:
        # Seed with initial period average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Wilder's exponential smoothing
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss < 1e-9:
        return 100.0 if avg_gain > 1e-9 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_realized_volatility(prices: List[float], period: int = 252) -> float:
    """
    Calculate annualized realized historical volatility: sqrt(252) * std(log_returns).
    """
    if len(prices) < 2:
        return 0.0

    sub = prices[-(period + 1):] if len(prices) > period + 1 else prices
    if len(sub) < 2:
        return 0.0

    log_returns: List[float] = []
    for i in range(1, len(sub)):
        if sub[i - 1] <= 0 or sub[i] <= 0:
            continue
        log_returns.append(math.log(sub[i] / sub[i - 1]))

    n = len(log_returns)
    if n < 2:
        return 0.0

    mean_ret = sum(log_returns) / n
    var = sum((r - mean_ret) ** 2 for r in log_returns) / (n - 1)
    std_dev = math.sqrt(var)

    return std_dev * math.sqrt(252.0)


def pct_change(prices: List[float], days: int = 20) -> float:
    if len(prices) <= days or prices[-days - 1] <= 0:
        return 0.0
    return prices[-1] / prices[-days - 1] - 1.0


def calculate_hv_distribution(
    prices: List[float],
    period: int = 20,
    lookback: int = 252,
) -> Tuple[float, Optional[float], float]:
    """Current HV, percentile vs trailing windows, and z-score."""
    need = period + 2
    if len(prices) < need:
        return 0.0, None, 0.0
    series: List[float] = []
    for end in range(period + 1, len(prices) + 1):
        hv = calculate_realized_volatility(prices[:end], period=period)
        if hv > 0:
            series.append(hv)
    if not series:
        return 0.0, None, 0.0
    current = series[-1]
    window = series[-lookback:] if len(series) > lookback else series
    count_less = sum(1 for v in window if v <= current)
    percentile = count_less / len(window)
    mean = sum(window) / len(window)
    if len(window) > 1:
        var = sum((v - mean) ** 2 for v in window) / (len(window) - 1)
        std = math.sqrt(var)
        z_score = (current - mean) / std if std > 1e-9 else 0.0
    else:
        z_score = 0.0
    return current, percentile, z_score


class PriceStore:
    """
    In-memory / local cache of daily price bars for technical indicator calculations.
    """
    def __init__(self):
        self._store: Dict[str, List[PriceBar]] = {}

    def add_bars(self, symbol: str, bars: List[PriceBar]) -> None:
        """Add or update sorted price bars for symbol."""
        existing = self._store.setdefault(symbol, [])
        existing.extend(bars)
        # Deduplicate and sort by trade_date
        seen_dates = set()
        deduped = []
        for b in sorted(existing, key=lambda x: x.trade_date):
            if b.trade_date not in seen_dates:
                seen_dates.add(b.trade_date)
                deduped.append(b)
        self._store[symbol] = deduped

    def get_bars(self, symbol: str) -> List[PriceBar]:
        return list(self._store.get(symbol, []))

    def get_metrics(self, symbol: str) -> PriceMetrics:
        """
        Compute daily metrics for symbol.
        Enforces minimum 200 bars requirement for valid Strategy 3 signals.
        """
        bars = self._store.get(symbol, [])
        bar_count = len(bars)

        if bar_count < 200:
            spot = bars[-1].close if bars else 0.0
            return PriceMetrics(
                symbol=symbol,
                spot=spot,
                rsi_14=50.0,
                sma_200=0.0,
                pct_to_200dma=0.0,
                high_52w=0.0,
                drawdown_52w_high=0.0,
                low_52w=0.0,
                bounce_52w_low=0.0,
                hv_252=0.0,
                hv_20=0.0,
                bar_count=bar_count,
                is_valid=False,
                pct_change_20d=0.0,
                hv_percentile=None,
                hv_z_score=0.0,
                reasons=["INSUFFICIENT_DAILY_BARS"]
            )

        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]

        spot = closes[-1]
        rsi_14 = calculate_rsi(closes, period=14)
        sma_200 = calculate_sma(closes, period=200)

        pct_to_200dma = (spot - sma_200) / sma_200 if sma_200 > 0 else 0.0

        # 52-week window (last 252 bars or all available bars)
        w52_bars = bars[-252:]
        high_52w = max(b.high for b in w52_bars)
        low_52w = min(b.low for b in w52_bars)

        drawdown_52w_high = (high_52w - spot) / high_52w if high_52w > 0 else 0.0
        bounce_52w_low = (spot - low_52w) / low_52w if low_52w > 0 else 0.0

        hv_252 = calculate_realized_volatility(closes, period=252)
        hv_20, hv_percentile, hv_z_score = calculate_hv_distribution(closes, period=20, lookback=252)
        if hv_20 <= 0:
            hv_20 = calculate_realized_volatility(closes, period=20)

        return PriceMetrics(
            symbol=symbol,
            spot=spot,
            rsi_14=rsi_14,
            sma_200=sma_200,
            pct_to_200dma=pct_to_200dma,
            high_52w=high_52w,
            drawdown_52w_high=drawdown_52w_high,
            low_52w=low_52w,
            bounce_52w_low=bounce_52w_low,
            hv_252=hv_252,
            hv_20=hv_20,
            bar_count=bar_count,
            is_valid=True,
            pct_change_20d=pct_change(closes, 20),
            hv_percentile=hv_percentile,
            hv_z_score=hv_z_score,
            reasons=[]
        )
