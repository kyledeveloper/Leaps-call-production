"""
Base data models and abstract provider interface.
Strictly adheres to:
- Global Invariant 3: Single asof snapshot clock
- Global Invariant 4: T = DTE / 365.25, multiplier = 100
- Global Invariant 5: Non-standard / adjusted contract isolation
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    underlying: str
    strike: float
    expiry: str
    dte: float
    bid: float
    ask: float
    open_interest: int
    volume: int
    avg_volume_20d: float = 0.0
    bid_size: int = 10
    ask_size: int = 10
    quote_ts: Optional[str] = None
    multiplier: int = 100
    is_adjusted: bool = False
    broker_iv: Optional[float] = None
    broker_delta: Optional[float] = None


@dataclass(frozen=True)
class UnderlyingQuote:
    symbol: str
    spot: float
    asof: str
    dividend_yield: float = 0.0
    rsi_14: Optional[float] = None
    sma_200: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    hv_252: Optional[float] = None


class OptionDataProvider(ABC):
    @abstractmethod
    async def get_underlying(self, symbol: str) -> UnderlyingQuote:
        pass

    @abstractmethod
    async def get_expirations(self, symbol: str) -> List[str]:
        pass

    @abstractmethod
    async def get_option_chain(self, symbol: str, expiry: str) -> List[OptionQuote]:
        pass
