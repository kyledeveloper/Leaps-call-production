"""
Public delayed market-data adapter (no API keys).

Yahoo Finance chart: spot, daily bars, trailing dividend yield.
Nasdaq OPRA delayed option chain: bid/ask/OI/volume for LEAPS expiries.
Cboe delayed CDN is attempted then ignored if blocked (current 403).

Personal-research use. Quotes are delayed, not NBBO.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.leaps_scanner.core.greeks import (
    calculate_american_greeks,
    calculate_american_put_greeks,
    calculate_put_fallback_delta
)
from src.leaps_scanner.core.iv_solver import solve_implied_volatility, solve_american_put_iv
from src.leaps_scanner.core.rates import RateCurve
from src.leaps_scanner.data.store.prices import PriceBar, PriceStore
from src.leaps_scanner.data.store.iv_history import IVDataPoint, IVHistoryStore
from src.leaps_scanner.data.store.daily_bars import DailyBarCache
from src.leaps_scanner.data.universe import CORE_ETFS, SymbologyNormalizer
from src.leaps_scanner.data.funnel import keep_scan_delta
from src.leaps_scanner.scoring.ranker import StrategyCandidate
from src.leaps_scanner.scoring.csp_ranker import CSPCandidate, EarningsStatus

logger = logging.getLogger(__name__)

_OCC_RE = re.compile(r"([A-Za-z.]+)-{2,3}(\d{6})([cCpP])(\d{8})")
_OCC_RE_COMPACT = re.compile(r"/([A-Za-z][A-Za-z0-9.]{0,5})(\d{6})([cCpP])(\d{8})")
_LAST_TRADE_RE = re.compile(r"\$([0-9,]+\.?[0-9]*)")
_ETF_SET = {s.upper() for s in CORE_ETFS}
_SSL = ssl.create_default_context()

FetchFn = Callable[[str, Dict[str, str]], Tuple[int, bytes]]
ProgressCb = Callable[[int, int, str, List[StrategyCandidate]], None]
StopFn = Callable[[], bool]


class RateLimiter:
    """Global minimum spacing between HTTP starts. Shared across worker threads."""

    def __init__(self, min_interval_s: float = 0.12):
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self, should_stop: Optional[StopFn] = None) -> None:
        if self.min_interval_s <= 0:
            return
        if should_stop and should_stop():
            return
        with self._lock:
            if should_stop and should_stop():
                return
            now = time.monotonic()
            start = max(self._next, now)
            self._next = start + self.min_interval_s
            delay = start - now
        if delay > 0:
            step = 0.05
            remaining = delay
            while remaining > 0:
                if should_stop and should_stop():
                    return
                sleep_time = min(step, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _default_fetch(url: str, headers: Dict[str, str]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=_SSL, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if exc.fp else b""


def _parse_num(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if text in ("", "--", "N/A", "na", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_occ_from_nasdaq_url(url: str) -> Optional[Tuple[str, str, str, float]]:
    """Parse OCC from Nasdaq drillDownURL. Returns (underlying, YYYY-MM-DD, C/P, strike)."""
    if not url:
        return None
    match = _OCC_RE.search(url) or _OCC_RE_COMPACT.search(url)
    if not match:
        return None
    und, yymmdd, cp, strike_raw = match.groups()
    year = 2000 + int(yymmdd[0:2])
    month = int(yymmdd[2:4])
    day = int(yymmdd[4:6])
    expiry = f"{year:04d}-{month:02d}-{day:02d}"
    strike = int(strike_raw) / 1000.0
    return und.upper(), expiry, cp.upper(), strike


def parse_expiry_date(raw: Any) -> Optional[str]:
    """Normalize Nasdaq expiry strings to YYYY-MM-DD."""
    if raw is None:
        return None
    text = str(raw).strip().replace("  ", " ")
    if not text or text in ("--", "N/A", "na", "None"):
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=((4 - first.weekday()) % 7) + 14)


def csp_target_expiries(
    asof: datetime,
    min_dte: float = 7.0,
    max_dte: float = 45.0,
    include_weeklies: bool = False,
) -> List[str]:
    """Expiries Nasdaq must be queried one-at-a-time (fromdate=todate=that day)."""
    start = (asof + timedelta(days=min_dte)).date()
    end = (asof + timedelta(days=max_dte)).date()
    out: List[str] = []
    y, m = start.year, start.month
    for _ in range(5):
        day = third_friday(y, m)
        if start <= day <= end:
            out.append(day.isoformat())
        m += 1
        if m > 12:
            y += 1
            m = 1
        if date(y, m, 1) > end:
            break
    if include_weeklies or not out:
        cursor = start
        while cursor.weekday() != 4:
            cursor += timedelta(days=1)
        while cursor <= end:
            iso = cursor.isoformat()
            if iso not in out:
                out.append(iso)
            cursor += timedelta(days=7)
        out.sort()
    return out


def _nasdaq_chain_headers(symbol: str) -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/option-chain",
        "Origin": "https://www.nasdaq.com",
    }


def occ_symbol(underlying: str, expiry: str, strike: float, cp: str = "C") -> str:
    root = underlying.replace(".", "").upper()
    y, m, d = expiry.split("-")
    yy = y[2:]
    return f"{root}{yy}{m}{d}{cp.upper()}{int(round(strike * 1000)):08d}"


def parse_nasdaq_last_trade(blob: Any) -> Optional[float]:
    if blob is None:
        return None
    if isinstance(blob, (int, float)):
        return float(blob)
    text = str(blob).strip().replace(",", "")
    if not text or text in ("--", "N/A", "na", "None"):
        return None
    direct = _parse_num(text)
    if direct is not None:
        return direct
    match = _LAST_TRADE_RE.search(text)
    if not match:
        return None
    return _parse_num(match.group(1))


def parse_nasdaq_chain(payload: dict, min_dte: float = 250.0, asof: Optional[datetime] = None) -> List[dict]:
    """Flatten Nasdaq option-chain JSON into call rows with ISO expiry."""
    asof = asof or datetime.now(timezone.utc)
    data = payload.get("data") or {}
    rows = ((data.get("table") or {}).get("rows")) or []
    out: List[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = parse_occ_from_nasdaq_url(row.get("drillDownURL") or "")
        if not parsed:
            continue
        und, expiry, cp, strike = parsed
        if cp != "C":
            continue
        try:
            exp_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        dte = (exp_dt - asof).total_seconds() / 86400.0
        if dte < min_dte:
            continue
        bid = _parse_num(row.get("c_Bid"))
        ask = _parse_num(row.get("c_Ask"))
        if bid is None or ask is None or bid < 0 or ask <= 0 or bid > ask:
            continue
        oi = int(_parse_num(row.get("c_Openinterest")) or 0)
        vol = int(_parse_num(row.get("c_Volume")) or 0)
        out.append({
            "underlying": und,
            "expiry": expiry,
            "strike": strike,
            "dte": dte,
            "bid": bid,
            "ask": ask,
            "open_interest": oi,
            "volume": vol,
            "symbol": occ_symbol(und, expiry, strike, "C"),
        })
    return out


def parse_nasdaq_csp_chain(
    payload: dict,
    min_dte: float = 7.0,
    max_dte: float = 45.0,
    asof: Optional[datetime] = None,
) -> List[dict]:
    """Flatten Nasdaq option-chain JSON into Put rows within 7~45 DTE."""
    asof = asof or datetime.now(timezone.utc)
    data = payload.get("data") or {}
    rows = ((data.get("table") or {}).get("rows")) or []
    out: List[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bid = _parse_num(row.get("p_Bid")) or _parse_num(row.get("p_Last"))
        ask = _parse_num(row.get("p_Ask"))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
            continue
        oi = int(_parse_num(row.get("p_Openinterest")) or 0)
        vol = int(_parse_num(row.get("p_Volume")) or 0)
        strike = _parse_num(row.get("strike"))
        parsed = parse_occ_from_nasdaq_url(row.get("drillDownURL") or "")
        und = ""
        expiry = None
        if parsed:
            und, expiry, _, parsed_strike = parsed
            if strike is None or strike <= 0:
                strike = parsed_strike
        if strike is None or strike <= 0:
            continue
        if not expiry:
            expiry = parse_expiry_date(
                row.get("putExpiryDate") or row.get("expiryDate") or row.get("expirationDate")
            )
        if not expiry:
            continue
        try:
            exp_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        dte = (exp_dt - asof).total_seconds() / 86400.0
        if dte < min_dte or dte > max_dte:
            continue
        out.append({
            "underlying": und,
            "expiry": expiry,
            "strike": strike,
            "dte": dte,
            "bid": bid,
            "ask": ask,
            "open_interest": oi,
            "volume": vol,
            "bid_size": int(_parse_num(row.get("p_BidSize") or row.get("p_bidSize")) or 0),
            "ask_size": int(_parse_num(row.get("p_AskSize") or row.get("p_askSize")) or 0),
            "symbol": occ_symbol(und, expiry, strike, "P") if und else f"PUT_{strike}_{expiry}",
        })
    return out


def parse_yahoo_chart(payload: dict) -> Tuple[float, List[PriceBar], float]:
    """Return (spot, daily bars, trailing dividend yield)."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return 0.0, [], 0.0
    block = result[0]
    meta = block.get("meta") or {}
    timestamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    opens = quote.get("open") or []
    volumes = quote.get("volume") or []
    bars: List[PriceBar] = []
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None or close <= 0:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        high = highs[i] if i < len(highs) and highs[i] else close
        low = lows[i] if i < len(lows) and lows[i] else close
        opn = opens[i] if i < len(opens) and opens[i] else close
        vol = volumes[i] if i < len(volumes) and volumes[i] else 0.0
        bars.append(PriceBar(trade_date=day, close=float(close), high=float(high), low=float(low), volume=float(vol), open=float(opn)))
    spot = float(meta.get("regularMarketPrice") or (bars[-1].close if bars else 0.0))
    events = (block.get("events") or {}).get("dividends") or {}
    cutoff = time.time() - 365.25 * 86400
    div_cash = 0.0
    for item in events.values() if isinstance(events, dict) else []:
        if not isinstance(item, dict):
            continue
        if float(item.get("date") or 0) >= cutoff:
            div_cash += float(item.get("amount") or 0.0)
    div_yield = (div_cash / spot) if spot > 0 else 0.0
    return spot, bars, div_yield


def parse_yahoo_earnings_dates(payload: dict) -> List[datetime]:
    """Future+past earnings timestamps from a Yahoo chart events blob."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    events = (result[0].get("events") or {}).get("earnings") or {}
    out: List[datetime] = []
    for item in events.values() if isinstance(events, dict) else []:
        if not isinstance(item, dict):
            continue
        ts = float(item.get("date") or 0)
        if ts <= 0:
            continue
        out.append(datetime.fromtimestamp(ts, tz=timezone.utc))
    return out


def classify_csp_earnings(
    is_etf: bool,
    asof: datetime,
    dte: float,
    earnings_dates: Optional[List[datetime]] = None,
) -> EarningsStatus:
    if is_etf:
        return EarningsStatus.CONFIRMED_SAFE
    if not earnings_dates:
        return EarningsStatus.EARNINGS_UNVERIFIED
    expiry = asof + timedelta(days=max(0.0, float(dte)))
    for ev in earnings_dates:
        if asof <= ev <= expiry:
            return EarningsStatus.EARNINGS_IMPACTED
    return EarningsStatus.CONFIRMED_SAFE


def _select_contracts(rows: List[dict], spot: float, max_n: int = 48) -> List[dict]:
    """Prefer the 0.65S-0.85S replacement zone, then fill from the union LEAPS window."""
    def moneyness(row: dict) -> float:
        return row["strike"] / spot if spot > 0 else 1.0

    def rank_key(target: float):
        def key(row: dict) -> Tuple[int, float]:
            return (-row["open_interest"], abs(moneyness(row) - target))
        return key

    in_s1 = [r for r in rows if 0.65 <= moneyness(r) <= 0.85]
    rest = [r for r in rows if r not in in_s1 and 0.65 <= moneyness(r) <= 1.25]
    half = max(max_n // 2, 8)
    chosen = sorted(in_s1, key=rank_key(0.75))[:half]
    leftover = max_n - len(chosen)
    if leftover > 0:
        chosen.extend(sorted(rest, key=rank_key(0.90))[:leftover])
    return chosen


def _select_csp_contracts(rows: List[dict], spot: float, max_n: int = 40) -> List[dict]:
    """Keep near-OTM puts (0.70S-1.02S) for 7-45 DTE CSP scans."""
    if spot <= 0:
        return []

    def moneyness(row: dict) -> float:
        return row["strike"] / spot

    preferred = [r for r in rows if 0.70 <= moneyness(r) <= 1.02]
    pool = preferred or [r for r in rows if 0.60 <= moneyness(r) <= 1.05]
    pool.sort(key=lambda r: (abs(moneyness(r) - 0.92), -r.get("open_interest", 0)))
    return pool[:max_n]


class PublicDelayedClient:
    """Duck-typed replacement for WebullClient.get_leaps_candidates."""

    app_key = None
    app_secret = None

    def __init__(
        self,
        fetch_fn: Optional[FetchFn] = None,
        iv_store: Optional[IVHistoryStore] = None,
        bar_cache: Optional[DailyBarCache] = None,
        max_workers: Optional[int] = None,
        min_interval_s: Optional[float] = None,
    ):
        self._custom_fetch = fetch_fn is not None
        self._fetch = fetch_fn or _default_fetch
        self.offline_mode = False
        self.source = "delayed"
        self.rates = RateCurve()
        self.iv_store = iv_store if iv_store is not None else IVHistoryStore()
        self.bar_cache = bar_cache
        default_workers = _env_int("LEAPS_FETCH_WORKERS", 8)
        default_interval = 0.0 if self._custom_fetch else _env_float("LEAPS_FETCH_MIN_INTERVAL", 0.12)
        self.max_workers = max(1, int(max_workers if max_workers is not None else default_workers))
        interval = default_interval if min_interval_s is None else min_interval_s
        self.rate_limiter = RateLimiter(interval)
        self._iv_lock = threading.Lock()
        self._earnings_dates: Dict[str, List[datetime]] = {}

    def _get_json(
        self,
        url: str,
        headers: Dict[str, str],
        retries: int = 2,
        should_stop: Optional[StopFn] = None,
    ) -> dict:
        last_err = "empty"
        for attempt in range(retries + 1):
            if should_stop and should_stop():
                raise InterruptedError("fetch cancelled")
            self.rate_limiter.wait(should_stop=should_stop)
            if should_stop and should_stop():
                raise InterruptedError("fetch cancelled")
            status, body = self._fetch(url, headers)
            if should_stop and should_stop():
                raise InterruptedError("fetch cancelled")
            if status == 429:
                backoff = 0.8 * (attempt + 1)
                step = 0.05
                remaining = backoff
                while remaining > 0:
                    if should_stop and should_stop():
                        raise InterruptedError("fetch cancelled")
                    sleep_time = min(step, remaining)
                    time.sleep(sleep_time)
                    remaining -= sleep_time
                last_err = "rate_limited"
                continue
            if status != 200 or not body:
                last_err = f"http_{status}"
                continue
            try:
                return json.loads(body.decode("utf-8"))
            except Exception as exc:
                last_err = str(type(exc).__name__)
        raise RuntimeError(f"public delayed fetch failed ({last_err}) for {url.split('?')[0]}")

    def _yahoo_chart(
        self,
        symbol: str,
        should_stop: Optional[StopFn] = None,
    ) -> Tuple[float, List[PriceBar], float]:
        if should_stop and should_stop():
            return 0.0, [], 0.0
        if self.bar_cache is not None:
            hit = self.bar_cache.get(symbol)
            if hit is not None:
                # Bars/div only. Pricing spot comes from Nasdaq lastTrade.
                return 0.0, list(hit.bars), hit.div_yield
        ticker = urllib.parse.quote(symbol)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?range=2y&interval=1d&events=div%2Cearn"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; LEAPSScanner/2.0)",
            "Accept": "application/json",
            "Referer": f"https://finance.yahoo.com/quote/{ticker}",
        }
        payload = self._get_json(url, headers, should_stop=should_stop)
        spot, bars, div_yield = parse_yahoo_chart(payload)
        self._earnings_dates[symbol.upper()] = parse_yahoo_earnings_dates(payload)
        if not (should_stop and should_stop()) and self.bar_cache is not None and bars:
            self.bar_cache.put(symbol, spot, bars, div_yield)
        return spot, bars, div_yield

    def _nasdaq_chain(
        self,
        symbol: str,
        asof: datetime,
        should_stop: Optional[StopFn] = None,
    ) -> Tuple[List[dict], Optional[float]]:
        if should_stop and should_stop():
            return [], None
        ticker = urllib.parse.quote(symbol)
        start = (asof + timedelta(days=250)).date().isoformat()
        end = (asof + timedelta(days=1100)).date().isoformat()
        last = None
        rows: List[dict] = []
        asset_classes = ["etf", "stocks"] if symbol.upper() in _ETF_SET else ["stocks", "etf"]
        for asset in asset_classes:
            if should_stop and should_stop():
                return [], None
            url = (
                "https://api.nasdaq.com/api/quote/"
                f"{ticker}/option-chain?assetclass={asset}&limit=0"
                f"&fromdate={start}&todate={end}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/option-chain",
                "Origin": "https://www.nasdaq.com",
            }
            try:
                payload = self._get_json(url, headers, should_stop=should_stop)
            except InterruptedError:
                return [], None
            except Exception:
                continue
            last = parse_nasdaq_last_trade((payload.get("data") or {}).get("lastTrade")) or last
            rows = parse_nasdaq_chain(payload, min_dte=250.0, asof=asof)
            if rows:
                break
        if not rows and last is None:
            if should_stop and should_stop():
                return [], None
            raise RuntimeError(f"nasdaq chain empty for {symbol}")
        return rows, last

    def _apply_iv_metrics(
        self,
        sym: str,
        symbol_rows: List[StrategyCandidate],
        atm_iv: Optional[float],
        hv: float,
        asof_day: str,
    ) -> None:
        with self._iv_lock:
            if atm_iv is not None:
                self.iv_store.add_data_point(sym, IVDataPoint(trade_date=asof_day, atm_iv=atm_iv))
            ivm = self.iv_store.get_metrics(sym, atm_iv if atm_iv is not None else hv)
        for cand in symbol_rows:
            if hasattr(cand, "iv_history_days"):
                cand.iv_history_days = ivm.valid_days
            if not ivm.is_degraded:
                cand.iv_percentile = ivm.iv_percentile
                cand.iv_rank = ivm.iv_rank
                if hasattr(cand, "iv_z_score"):
                    cand.iv_z_score = ivm.iv_z_score

    def _scan_symbol(
        self,
        raw: str,
        asof: datetime,
        should_stop: Optional[StopFn] = None,
    ) -> Tuple[str, List[StrategyCandidate]]:
        sym = SymbologyNormalizer.to_canonical(raw)
        if should_stop and should_stop():
            return sym, []
        try:
            spot, bars, div_yield = self._yahoo_chart(sym, should_stop=should_stop)
        except InterruptedError:
            return sym, []
        except Exception as exc:
            logger.warning("Yahoo chart failed for %s: %s", sym, exc)
            spot, bars, div_yield = 0.0, [], 0.0
        if should_stop and should_stop():
            return sym, []
        store = PriceStore()
        if bars:
            store.add_bars(sym, bars)
        if should_stop and should_stop():
            return sym, []
        try:
            rows, nasdaq_spot = self._nasdaq_chain(sym, asof, should_stop=should_stop)
        except InterruptedError:
            return sym, []
        except Exception as exc:
            logger.warning("Nasdaq chain failed for %s: %s", sym, exc)
            return sym, []
        if should_stop and should_stop():
            return sym, []
        if nasdaq_spot and nasdaq_spot > 0:
            spot = nasdaq_spot
        elif spot <= 0 and bars:
            spot = float(bars[-1].close)
        if spot <= 0:
            return sym, []
        metrics = store.get_metrics(sym, spot_override=spot) if bars else None
        chosen = _select_contracts(rows, spot)
        is_etf = sym.upper() in _ETF_SET
        hv = metrics.hv_252 if metrics and metrics.hv_252 > 0 else 0.25
        hv20 = metrics.hv_20 if metrics and metrics.hv_20 > 0 else hv
        hv_pct = metrics.hv_percentile if metrics else None
        hv_z = metrics.hv_z_score if metrics else 0.0
        chg20 = metrics.pct_change_20d if metrics else 0.0
        rsi = metrics.rsi_14 if metrics else 50.0
        dma = metrics.pct_to_200dma if metrics else 0.0
        dd = metrics.drawdown_52w_high if metrics else 0.0
        bounce = metrics.bounce_52w_low if metrics else 0.0
        hist_days = metrics.bar_count if metrics else 0
        symbol_rows: List[StrategyCandidate] = []
        atm_iv: Optional[float] = None
        atm_dist = 1e9
        for row in chosen:
            if should_stop and should_stop():
                return sym, []
            if row["strike"] / spot < 0.65:
                continue
            t_years = row["dte"] / 365.25
            r = self.rates.get_rate(t_years)
            mid = (row["bid"] + row["ask"]) / 2.0
            iv_res = solve_implied_volatility(
                mid, spot, row["strike"], t_years, r, div_yield, initial_guess=max(0.12, hv)
            )
            iv = iv_res.iv if iv_res.iv is not None else (hv if hv > 0 else None)
            greeks_vol = iv if iv is not None else hv
            greeks_vol = max(float(greeks_vol or 0.0), 0.20)
            try:
                greeks = calculate_american_greeks(spot, row["strike"], t_years, r, div_yield, greeks_vol)
                delta = greeks.delta
            except Exception:
                delta = max(0.05, min(0.95, 0.5 + 0.5 * (spot - row["strike"]) / max(spot, 1.0)))
            if not keep_scan_delta(delta):
                continue
            if iv is not None:
                dist = abs(row["strike"] / spot - 1.0)
                if dist < atm_dist:
                    atm_dist = dist
                    atm_iv = float(iv)
            symbol_rows.append(StrategyCandidate(
                symbol=row["symbol"],
                underlying=sym.upper(),
                strike=row["strike"],
                spot=spot,
                dte=row["dte"],
                bid=row["bid"],
                ask=row["ask"],
                delta=delta,
                open_interest=row["open_interest"],
                volume=row["volume"],
                dividend_yield=div_yield,
                iv=iv,
                iv_percentile=None,
                iv_rank=None,
                rsi_14=rsi,
                pct_to_200dma=dma,
                drawdown_52w_high=dd,
                bounce_52w_low=bounce,
                is_etf=is_etf,
                hv_252=hv,
                hv_20=hv20,
                hv_percentile=hv_pct,
                hv_z_score=hv_z,
                pct_change_20d=chg20,
                valid_history_days=hist_days,
                data_quality="DELAYED",
            ))
        if should_stop and should_stop():
            return sym, []
        self._apply_iv_metrics(sym, symbol_rows, atm_iv, hv, asof.date().isoformat())
        return sym, symbol_rows

    def get_leaps_candidates(
        self,
        symbols: List[str],
        progress_cb: Optional[ProgressCb] = None,
        should_stop: Optional[StopFn] = None,
    ) -> List[StrategyCandidate]:
        asof = datetime.now(timezone.utc)
        names = [SymbologyNormalizer.to_canonical(s) for s in symbols]
        total = len(names)
        out: List[StrategyCandidate] = []
        if total == 0:
            return out

        completed = 0
        collect_lock = threading.Lock()
        workers = min(self.max_workers, total)

        def consume(sym: str, batch: List[StrategyCandidate]) -> None:
            nonlocal completed
            with collect_lock:
                completed += 1
                done = completed
                out.extend(batch)
            if progress_cb:
                try:
                    progress_cb(done, total, sym, batch)
                except Exception as cb_err:
                    logger.warning("Progress callback error for %s: %s", sym, cb_err)

        if workers <= 1:
            for raw in names:
                if should_stop and should_stop():
                    break
                sym, batch = self._scan_symbol(raw, asof, should_stop=should_stop)
                consume(sym, batch)
        else:
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = {
                    pool.submit(self._scan_symbol, raw, asof, should_stop): raw
                    for raw in names
                }
                for fut in as_completed(futures):
                    if should_stop and should_stop():
                        for pending in futures:
                            pending.cancel()
                        break
                    try:
                        sym, batch = fut.result()
                    except Exception as exc:
                        raw = futures[fut]
                        logger.warning("Delayed scan failed for %s: %s", raw, exc)
                        consume(str(raw), [])
                        continue
                    consume(sym, batch)
            finally:
                for pending in futures:
                    pending.cancel()
                # Clean shutdown waiting for active cooperative checkpoints so no zombie threads escape
                pool.shutdown(wait=True, cancel_futures=True)

        if not (should_stop and should_stop()):
            try:
                self.iv_store.flush()
            except Exception as exc:
                logger.warning("Failed to flush iv_store: %s", exc)
            if self.bar_cache is not None:
                try:
                    self.bar_cache.flush()
                except Exception as exc:
                    logger.warning("Failed to flush bar_cache: %s", exc)
        return out

    def _nasdaq_csp_chain(
        self,
        symbol: str,
        asof: datetime,
        should_stop: Optional[StopFn] = None,
    ) -> Tuple[List[dict], Optional[float]]:
        """Nasdaq returns one expiry per request. Pin fromdate=todate to each monthly Friday."""
        if should_stop and should_stop():
            return [], None
        ticker = urllib.parse.quote(symbol)
        headers = _nasdaq_chain_headers(symbol)
        asset_classes = ["etf", "stocks"] if symbol.upper() in _ETF_SET else ["stocks", "etf"]
        last = None
        collected: List[dict] = []
        seen: set = set()
        monthlies = csp_target_expiries(asof, min_dte=7.0, max_dte=45.0, include_weeklies=False)
        weeklies = [
            d for d in csp_target_expiries(asof, min_dte=7.0, max_dte=21.0, include_weeklies=True)
            if d not in monthlies
        ]
        expiries = monthlies + weeklies

        def ingest(payload: dict) -> None:
            nonlocal last
            last = parse_nasdaq_last_trade((payload.get("data") or {}).get("lastTrade")) or last
            for row in parse_nasdaq_csp_chain(payload, min_dte=7.0, max_dte=45.0, asof=asof):
                key = (row.get("expiry"), row.get("strike"), row.get("symbol"))
                if key in seen:
                    continue
                seen.add(key)
                collected.append(row)

        for asset in asset_classes:
            if collected:
                break
            for expiry in expiries:
                if should_stop and should_stop():
                    return collected, last
                url = (
                    "https://api.nasdaq.com/api/quote/"
                    f"{ticker}/option-chain?assetclass={asset}&limit=0"
                    f"&fromdate={expiry}&todate={expiry}"
                )
                try:
                    payload = self._get_json(url, headers, should_stop=should_stop)
                except InterruptedError:
                    return collected, last
                except Exception:
                    continue
                ingest(payload)

            if collected:
                break
            # Front-week-only responses: walk every Friday in the CSP window.
            for expiry in csp_target_expiries(asof, min_dte=7.0, max_dte=45.0, include_weeklies=True):
                if expiry in expiries:
                    continue
                if should_stop and should_stop():
                    return collected, last
                url = (
                    "https://api.nasdaq.com/api/quote/"
                    f"{ticker}/option-chain?assetclass={asset}&limit=0"
                    f"&fromdate={expiry}&todate={expiry}"
                )
                try:
                    payload = self._get_json(url, headers, should_stop=should_stop)
                except InterruptedError:
                    return collected, last
                except Exception:
                    continue
                ingest(payload)
                if collected:
                    break
        return collected, last

    def _scan_csp_symbol(
        self,
        raw: str,
        asof: datetime,
        should_stop: Optional[StopFn] = None,
    ) -> Tuple[str, List[CSPCandidate]]:
        sym = SymbologyNormalizer.to_canonical(raw)
        if should_stop and should_stop():
            return sym, []
        try:
            spot, bars, div_yield = self._yahoo_chart(sym, should_stop=should_stop)
        except InterruptedError:
            return sym, []
        except Exception as exc:
            logger.warning("Yahoo chart failed for %s: %s", sym, exc)
            spot, bars, div_yield = 0.0, [], 0.0
        if should_stop and should_stop():
            return sym, []
        store = PriceStore()
        if bars:
            store.add_bars(sym, bars)
        try:
            rows, nasdaq_spot = self._nasdaq_csp_chain(sym, asof, should_stop=should_stop)
        except InterruptedError:
            return sym, []
        except Exception as exc:
            logger.warning("Nasdaq CSP chain failed for %s: %s", sym, exc)
            return sym, []
        if should_stop and should_stop():
            return sym, []
        if nasdaq_spot and nasdaq_spot > 0:
            spot = nasdaq_spot
        elif spot <= 0 and bars:
            spot = float(bars[-1].close)
        if spot <= 0:
            return sym, []
        rows = _select_csp_contracts(rows, spot)
        metrics = store.get_metrics(sym, spot_override=spot) if bars else None
        is_etf = sym.upper() in _ETF_SET
        hv = metrics.hv_252 if metrics and metrics.hv_252 > 0 else 0.25
        rsi = metrics.rsi_14 if metrics else 50.0
        dma = metrics.pct_to_200dma if metrics else 0.0

        symbol_rows: List[CSPCandidate] = []
        for row in rows:
            if should_stop and should_stop():
                return sym, []
            strike = row["strike"]
            if strike > 1.02 * spot or strike < 0.60 * spot:
                continue
            t_years = row["dte"] / 365.25
            r = self.rates.get_rate(t_years)
            mid = (row["bid"] + row["ask"]) / 2.0
            iv_res = solve_american_put_iv(
                spot, strike, t_years, r, div_yield, mid, initial_guess=max(0.12, hv)
            )
            iv = iv_res.iv if iv_res.iv is not None else (hv if hv > 0 else 0.25)
            try:
                greeks = calculate_american_put_greeks(spot, strike, t_years, r, div_yield, iv)
                delta = greeks.delta if greeks.delta <= 0.0 else -abs(greeks.delta)
            except Exception:
                delta = calculate_put_fallback_delta(spot, strike)
            if delta >= 0.0 or delta < -1.0:
                continue
            earnings_status = classify_csp_earnings(
                is_etf, asof, row["dte"], self._earnings_dates.get(sym.upper(), [])
            )
            symbol_rows.append(CSPCandidate(
                symbol=row["symbol"],
                underlying=sym.upper(),
                spot=spot,
                strike=strike,
                dte=row["dte"],
                bid=row["bid"],
                ask=row["ask"],
                delta=delta,
                open_interest=row["open_interest"],
                volume=row["volume"],
                iv=iv,
                iv_rank=None,
                iv_percentile=None,
                rsi_14=rsi,
                pct_to_200dma=dma,
                earnings_status=earnings_status,
                is_etf=is_etf,
                bid_size=int(row.get("bid_size") or 0),
                ask_size=int(row.get("ask_size") or 0),
            ))
        if symbol_rows:
            atm = min(symbol_rows, key=lambda c: abs(c.strike / spot - 1.0) if spot else 1.0)
            self._apply_iv_metrics(sym, symbol_rows, atm.iv, hv, asof.date().isoformat())
        return sym, symbol_rows

    def get_csp_candidates(
        self,
        symbols: List[str],
        progress_cb: Optional[Callable[[int, int, str, List[CSPCandidate]], None]] = None,
        should_stop: Optional[StopFn] = None,
    ) -> List[CSPCandidate]:
        """
        Concurrently fetch CSP candidates (7~45 DTE Puts) across symbols.
        Uses ThreadPoolExecutor for multi-threaded parallel scanning.
        """
        asof = datetime.now(timezone.utc)
        names = [SymbologyNormalizer.to_canonical(s) for s in symbols]
        total = len(names)
        out: List[CSPCandidate] = []
        if total == 0:
            return out

        completed = 0
        collect_lock = threading.Lock()
        workers = min(self.max_workers, total)

        def consume(sym: str, batch: List[CSPCandidate]) -> None:
            nonlocal completed
            with collect_lock:
                completed += 1
                done = completed
                out.extend(batch)
            if progress_cb:
                try:
                    progress_cb(done, total, sym, batch)
                except Exception as cb_err:
                    logger.warning("CSP progress callback error for %s: %s", sym, cb_err)

        if workers <= 1:
            for raw in names:
                if should_stop and should_stop():
                    break
                sym, batch = self._scan_csp_symbol(raw, asof, should_stop=should_stop)
                consume(sym, batch)
        else:
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = {
                    pool.submit(self._scan_csp_symbol, raw, asof, should_stop): raw
                    for raw in names
                }
                for fut in as_completed(futures):
                    if should_stop and should_stop():
                        for pending in futures:
                            pending.cancel()
                        break
                    try:
                        sym, batch = fut.result()
                    except Exception as exc:
                        raw = futures[fut]
                        logger.warning("Delayed CSP scan failed for %s: %s", raw, exc)
                        consume(str(raw), [])
                        continue
                    consume(sym, batch)
            finally:
                for pending in futures:
                    pending.cancel()
                pool.shutdown(wait=True, cancel_futures=True)

        return out
