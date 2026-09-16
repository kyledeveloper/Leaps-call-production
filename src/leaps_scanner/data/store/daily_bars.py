"""
Session-dated daily bar cache for Delayed public scans.

Yahoo 1d history is reused until the US equity session date rolls.
Thread-safe. JSON persist is optional (tests stay in-memory).
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.leaps_scanner.data.store.prices import PriceBar


def market_session_date(now: Optional[datetime] = None) -> str:
    """US/Eastern calendar date (falls back to UTC if tzdata is missing)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return now.astimezone(timezone.utc).date().isoformat()


def default_daily_bar_cache_path() -> Path:
    override = os.environ.get("LEAPS_DAILY_BAR_CACHE_PATH")
    if override:
        return Path(override)
    repo = Path(__file__).resolve().parents[4]
    return repo / "data" / "daily_bars.json"


@dataclass(frozen=True)
class DailyBarSnapshot:
    symbol: str
    session_date: str
    spot: float
    div_yield: float
    bars: List[PriceBar]


class DailyBarCache:
    """In-memory + optional disk cache keyed by canonical symbol."""

    def __init__(
        self,
        persist_path: Optional[str] = None,
        session_date_fn: Optional[Callable[[], str]] = None,
    ):
        self._store: Dict[str, DailyBarSnapshot] = {}
        self._path = Path(persist_path) if persist_path else None
        self._session_date_fn = session_date_fn or market_session_date
        self._lock = threading.Lock()
        if self._path:
            self.load()

    def current_session(self) -> str:
        return self._session_date_fn()

    def get(self, symbol: str) -> Optional[DailyBarSnapshot]:
        key = str(symbol).upper()
        session = self.current_session()
        with self._lock:
            snap = self._store.get(key)
            if snap is None or snap.session_date != session or not snap.bars:
                return None
            return snap

    def put(
        self,
        symbol: str,
        spot: float,
        bars: List[PriceBar],
        div_yield: float,
        session_date: Optional[str] = None,
    ) -> DailyBarSnapshot:
        key = str(symbol).upper()
        snap = DailyBarSnapshot(
            symbol=key,
            session_date=session_date or self.current_session(),
            spot=float(spot or 0.0),
            div_yield=float(div_yield or 0.0),
            bars=list(bars or []),
        )
        with self._lock:
            self._store[key] = snap
        return snap

    def load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        loaded: Dict[str, DailyBarSnapshot] = {}
        for sym, row in (raw or {}).items():
            if not isinstance(row, dict):
                continue
            try:
                bars = [
                    PriceBar(
                        trade_date=str(b["trade_date"]),
                        close=float(b["close"]),
                        high=float(b.get("high") or b["close"]),
                        low=float(b.get("low") or b["close"]),
                        volume=float(b.get("volume") or 0.0),
                        open=float(b["open"]) if b.get("open") is not None else None,
                    )
                    for b in (row.get("bars") or [])
                    if isinstance(b, dict) and b.get("trade_date") and b.get("close") is not None
                ]
            except (KeyError, TypeError, ValueError):
                continue
            loaded[str(sym).upper()] = DailyBarSnapshot(
                symbol=str(sym).upper(),
                session_date=str(row.get("session_date") or ""),
                spot=float(row.get("spot") or 0.0),
                div_yield=float(row.get("div_yield") or 0.0),
                bars=bars,
            )
        with self._lock:
            self._store = loaded

    def flush(self) -> None:
        if not self._path:
            return
        with self._lock:
            payload = {
                sym: {
                    "session_date": snap.session_date,
                    "spot": snap.spot,
                    "div_yield": snap.div_yield,
                    "bars": [asdict(b) for b in snap.bars],
                }
                for sym, snap in self._store.items()
            }
            path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
