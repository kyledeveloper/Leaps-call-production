"""
Stock and ETF Universe Definitions.
Contains authoritative constituent lists for S&P 100, Nasdaq 100, and Core Sector ETFs.
Enforces institutional liquidity constraints for LEAPS Call scanning.
"""
from typing import List, Set


# 1. Core Market and Sector ETFs (12)
CORE_ETFS: List[str] = [
    "SPY",   # S&P 500 ETF
    "QQQ",   # Nasdaq 100 ETF
    "IWM",   # Russell 2000 ETF
    "DIA",   # Dow Jones Industrial Average ETF
    "SMH",   # Semiconductor ETF
    "XLF",   # Financial Sector SPDR
    "XLK",   # Technology Sector SPDR
    "XLE",   # Energy Sector SPDR
    "XLV",   # Health Care Sector SPDR
    "XLI",   # Industrial Sector SPDR
    "XLY",   # Consumer Discretionary SPDR
    "XLP",   # Consumer Staples SPDR
]

# 2. S&P 100 (OEX) Full Constituents (101 tickers representing 100 top companies)
SP100_COMPONENTS: List[str] = [
    "AAPL", "ABBV", "ABNB", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT",
    "AMZN", "AVGO", "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK.B",
    "C", "CAT", "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO",
    "CVS", "CVX", "DE", "DHR", "DIS", "DOW", "DUK", "DVN", "EMR", "F",
    "FDX", "GD", "GE", "GILD", "GM", "GOOG", "GOOGL", "GS", "HD", "HON",
    "IBM", "INTC", "INTU", "ISRG", "JNJ", "JPM", "KDP", "KHC", "KO", "LIN",
    "LLY", "LMT", "LOW", "MA", "MCD", "MDT", "MET", "META", "MMM", "MO",
    "MRK", "MS", "MSFT", "NEE", "NFLX", "NKE", "NVDA", "ORCL", "PEP", "PFE",
    "PG", "PM", "PYPL", "QCOM", "REGN", "RTX", "SBUX", "SCHW", "SO", "SPGI",
    "T", "TGT", "TJX", "TMO", "TMUS", "TSLA", "TXN", "UNH", "UNP", "UPS",
    "USB", "V", "VZ", "WFC", "WMT", "XOM"
]

# 3. Nasdaq 100 (NDX) Full Constituents (101 tickers representing 100 non-financial tech/growth leaders)
NASDAQ100_COMPONENTS: List[str] = [
    "AAPL", "ABNB", "ADBE", "ADI", "ADP", "ADSK", "AEP", "ALGN", "AMAT", "AMD",
    "AMGN", "AMZN", "ANSS", "ASML", "AVGO", "AXON", "AZN", "BIIB", "BKNG", "BKR",
    "CDNS", "CEG", "CHTR", "CMCSA", "COST", "CPRT", "CRWD", "CSCO", "CSGP", "CSX",
    "CTAS", "CTSH", "DASH", "DDOG", "DLTR", "DXCM", "EA", "EXC", "FANG", "FAST",
    "FSLR", "FTNT", "GEV", "GILD", "GOOG", "GOOGL", "HON", "IDXX", "ILMN", "INTC",
    "INTU", "ISRG", "KDP", "KHC", "KLAC", "LRCX", "LULU", "MAR", "MCHP", "MDLZ",
    "MELI", "META", "MNST", "MRNA", "MRVL", "MSFT", "MSTR", "MU", "NFLX", "NVDA",
    "NXPI", "ODFL", "ON", "ORLY", "PANW", "PAYX", "PCAR", "PDD", "PEP", "PYPL",
    "QCOM", "REGN", "ROP", "ROST", "SBUX", "SMCI", "SNPS", "TEAM", "TMUS", "TSLA",
    "TTD", "TXN", "VOD", "VRSK", "VRTX", "WBD", "WDAY", "XEL", "ZS",
    "ARM", "PLTR"
]

# 4. Dow Jones Industrial Average (DJIA / Dow 30) Full Constituents (30 tickers)
# Reflects current S&P Dow Jones index composition (including NVDA, SHW added Nov 2024)
DJIA_COMPONENTS: List[str] = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
    "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT"
]

# 5. Top Liquid Mega-Cap Chinese ADRs (Included for broad multi-market coverage)
SELECTED_ADRS: List[str] = [
    "BABA", "PDD", "BIDU", "NIO", "LI", "JD"
]

# 6. Authoritative Deduplicated Full Core Universe (Set of all eligible symbols)
FULL_CORE_UNIVERSE: Set[str] = (
    set(SP100_COMPONENTS) |
    set(NASDAQ100_COMPONENTS) |
    set(DJIA_COMPONENTS) |
    set(CORE_ETFS) |
    set(SELECTED_ADRS)
)


class SymbologyNormalizer:
    """
    Normalizes stock and option ticker symbols across heterogeneous conventions.
    Canonical format uses dot notation for multi-class shares (e.g. BRK.B, BF.B).
    Broker format converts to vendor-specific requirements (e.g. Webull requires BRK-B).
    """
    @staticmethod
    def to_canonical(symbol: str) -> str:
        s = symbol.strip().upper()
        # Convert separators to canonical dot notation
        s = s.replace("-", ".").replace("/", ".")
        return s

    @staticmethod
    def to_broker(symbol: str, broker: str = "webull") -> str:
        canonical = SymbologyNormalizer.to_canonical(symbol)
        if broker.lower() == "webull":
            # Webull API requires hyphen for multi-class equities (BRK-B, BF-B)
            return canonical.replace(".", "-")
        return canonical


def get_universe(category: str = "all") -> List[str]:
    """
    Retrieve sorted ticker list by category.
    Categories: 'sp100', 'nasdaq100', 'djia', 'etfs', 'adrs', 'all'
    """
    cat = category.lower().strip()
    if cat == "sp100":
        return sorted(list(set(SP100_COMPONENTS)))
    elif cat == "nasdaq100":
        return sorted(list(set(NASDAQ100_COMPONENTS)))
    elif cat == "djia":
        return sorted(list(set(DJIA_COMPONENTS)))
    elif cat == "etfs":
        return sorted(list(set(CORE_ETFS)))
    elif cat == "adrs":
        return sorted(list(set(SELECTED_ADRS)))
    else:
        return sorted(list(FULL_CORE_UNIVERSE))


def is_in_core_universe(symbol: str) -> bool:
    """Check if symbol belongs to the institutional core universe."""
    canonical = SymbologyNormalizer.to_canonical(symbol)
    return canonical in FULL_CORE_UNIVERSE

