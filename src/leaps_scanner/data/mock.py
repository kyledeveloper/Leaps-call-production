"""
Mock Option Data Provider for hermetic sandbox testing.
Generates realistic multi-expiry option chains, historical indicators, and chaos edge cases.
"""
from typing import Dict, List
from src.leaps_scanner.data.base import OptionDataProvider, OptionQuote, UnderlyingQuote


class MockOptionDataProvider(OptionDataProvider):
    def __init__(self, asof: str = "2026-09-15T16:00:00Z"):
        self.asof = asof
        self._underlyings: Dict[str, UnderlyingQuote] = {
            "AAPL": UnderlyingQuote(
                symbol="AAPL",
                spot=220.0,
                asof=asof,
                dividend_yield=0.005,
                rsi_14=28.5,            # Oversold core signal
                sma_200=245.0,          # -10.2% below 200DMA
                high_52w=260.0,
                low_52w=180.0,
                hv_252=0.22
            ),
            "SPY": UnderlyingQuote(
                symbol="SPY",
                spot=550.0,
                asof=asof,
                dividend_yield=0.013,
                rsi_14=42.0,
                sma_200=520.0,
                high_52w=565.0,
                low_52w=480.0,
                hv_252=0.14
            ),
            "NVDA": UnderlyingQuote(
                symbol="NVDA",
                spot=120.0,
                asof=asof,
                dividend_yield=0.001,
                rsi_14=48.0,
                sma_200=110.0,
                high_52w=140.0,
                low_52w=75.0,
                hv_252=0.45
            )
        }

    async def get_underlying(self, symbol: str) -> UnderlyingQuote:
        if symbol in self._underlyings:
            return self._underlyings[symbol]
        return UnderlyingQuote(symbol=symbol, spot=100.0, asof=self.asof)

    async def get_expirations(self, symbol: str) -> List[str]:
        # Far-dated expirations (DTE >= 250d)
        return ["2026-06-19", "2027-01-15", "2027-06-18"]

    async def get_option_chain(self, symbol: str, expiry: str) -> List[OptionQuote]:
        underlying = await self.get_underlying(symbol)
        s = underlying.spot
        dte = 450.0 if expiry == "2027-01-15" else (300.0 if expiry == "2026-06-19" else 650.0)

        chain: List[OptionQuote] = []

        # Strikes from 0.50S to 1.35S
        # 1. Deep ITM candidates (e.g. 0.55S, 0.60S, 0.70S, 0.80S)
        deep_itm_strike = round(s * 0.60, 2)
        intrinsic = s - deep_itm_strike
        chain.append(
            OptionQuote(
                symbol=f"{symbol}_{expiry}_C_{deep_itm_strike}",
                underlying=symbol,
                strike=deep_itm_strike,
                expiry=expiry,
                dte=dte,
                bid=round(intrinsic + 3.0, 2),
                ask=round(intrinsic + 3.4, 2),
                open_interest=1200,
                volume=85,
                avg_volume_20d=45.0,
                bid_size=25,
                ask_size=30,
                quote_ts=self.asof,
                multiplier=100,
                is_adjusted=False
            )
        )

        # 2. Moderate ITM candidate (e.g. 0.80S)
        itm_strike = round(s * 0.80, 2)
        intrinsic_itm = s - itm_strike
        chain.append(
            OptionQuote(
                symbol=f"{symbol}_{expiry}_C_{itm_strike}",
                underlying=symbol,
                strike=itm_strike,
                expiry=expiry,
                dte=dte,
                bid=round(intrinsic_itm + 7.5, 2),
                ask=round(intrinsic_itm + 8.2, 2),
                open_interest=2500,
                volume=180,
                avg_volume_20d=90.0,
                bid_size=50,
                ask_size=50,
                quote_ts=self.asof,
                multiplier=100,
                is_adjusted=False
            )
        )

        # 3. ATM candidate (0.98S ~ 1.02S)
        atm_strike = round(s * 1.0, 2)
        chain.append(
            OptionQuote(
                symbol=f"{symbol}_{expiry}_C_{atm_strike}",
                underlying=symbol,
                strike=atm_strike,
                expiry=expiry,
                dte=dte,
                bid=14.5,
                ask=15.2,
                open_interest=5400,
                volume=620,
                avg_volume_20d=350.0,
                bid_size=100,
                ask_size=100,
                quote_ts=self.asof,
                multiplier=100,
                is_adjusted=False
            )
        )

        # High-volume far-dated contract (liquidity / activity fixture)
        otm_strike = round(s * 1.15, 2)
        chain.append(
            OptionQuote(
                symbol=f"{symbol}_{expiry}_C_{otm_strike}",
                underlying=symbol,
                strike=otm_strike,
                expiry=expiry,
                dte=dte,
                bid=6.8,
                ask=7.2,
                open_interest=150,     # Small initial OI
                volume=800,           # Vol / OI = 800 / 150 = 5.33x, Vol * Mid * 100 = 800 * 7 * 100 = $560k
                avg_volume_20d=40.0,
                bid_size=40,
                ask_size=40,
                quote_ts=self.asof,
                multiplier=100,
                is_adjusted=False
            )
        )

        # 5. Chaos / Non-standard fixtures for guard testing
        chain.append(
            OptionQuote(
                symbol=f"{symbol}_{expiry}_C_CHAOS_NONSTANDARD",
                underlying=symbol,
                strike=round(s * 0.90, 2),
                expiry=expiry,
                dte=dte,
                bid=10.0,
                ask=10.5,
                open_interest=500,
                volume=50,
                multiplier=1000,       # Non-standard multiplier
                quote_ts=self.asof
            )
        )
        chain.append(
            OptionQuote(
                symbol=f"{symbol}_{expiry}_C_CHAOS_ZEROBID",
                underlying=symbol,
                strike=round(s * 1.50, 2),
                expiry=expiry,
                dte=dte,
                bid=0.0,              # Zero bid
                ask=15.0,
                open_interest=10,      # Low OI
                volume=0,
                quote_ts=self.asof
            )
        )

        return chain
