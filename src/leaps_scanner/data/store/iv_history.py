"""
Historical ATM IV data store and statistics calculator.
Computes IV Percentile, IV Rank, and IV Z-Score over rolling windows.
Adheres strictly to Defensive Clause 5 (minimum 90 days threshold, STRATEGY_DEGRADED fallback).
Persists to JSON when persist_path is set so Delayed/Webull scans accumulate a 90d warehouse.
"""
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from src.leaps_scanner.strategies.guards import GuardStatus


def default_iv_history_path() -> Path:
    override = os.environ.get("LEAPS_IV_HISTORY_PATH")
    if override:
        return Path(override)
    repo = Path(__file__).resolve().parents[4]
    return repo / "data" / "iv_history.json"


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
    ATM IV warehouse. Memory-only unless persist_path is provided.
    """
    def __init__(self, persist_path: Optional[str] = None):
        self._store: Dict[str, List[IVDataPoint]] = {}
        self._path = Path(persist_path) if persist_path else None
        if self._path:
            self.load()

    def load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        loaded: Dict[str, List[IVDataPoint]] = {}
        for sym, rows in (raw or {}).items():
            pts = []
            for row in rows or []:
                try:
                    pts.append(IVDataPoint(trade_date=str(row["trade_date"]), atm_iv=float(row["atm_iv"])))
                except (KeyError, TypeError, ValueError):
                    continue
            loaded[str(sym).upper()] = pts
        self._store = loaded

    def flush(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            sym: [{"trade_date": p.trade_date, "atm_iv": p.atm_iv} for p in pts]
            for sym, pts in self._store.items()
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def add_data_point(self, symbol: str, point: IVDataPoint) -> None:
        existing = self._store.setdefault(symbol.upper(), [])
        existing.append(point)
        seen_dates = set()
        deduped = []
        for p in sorted(existing, key=lambda x: x.trade_date):
            if p.trade_date not in seen_dates:
                seen_dates.add(p.trade_date)
                deduped.append(p)
        self._store[symbol.upper()] = deduped

    def ingest_from_candidates(self, candidates: Iterable[Any], trade_date: str) -> None:
        """Keep the IV of the contract closest to ATM for each underlying, then flush."""
        best: Dict[str, tuple] = {}
        for c in candidates:
            iv = getattr(c, "iv", None)
            spot = float(getattr(c, "spot", 0.0) or 0.0)
            strike = float(getattr(c, "strike", 0.0) or 0.0)
            if iv is None or spot <= 0 or strike <= 0:
                continue
            dist = abs(strike / spot - 1.0)
            sym = str(getattr(c, "underlying", "")).upper()
            prev = best.get(sym)
            if prev is None or dist < prev[0]:
                best[sym] = (dist, float(iv))
        for sym, (_dist, iv) in best.items():
            self.add_data_point(sym, IVDataPoint(trade_date=trade_date, atm_iv=iv))
        self.flush()

    def get_metrics(self, symbol: str, current_iv: float, window: int = 252) -> IVMetrics:
        """
        Calculate IV percentile, rank, and z-score against historical ATM IVs.
        Enforces 90-day minimum valid history guard.
        """
        history = self._store.get(symbol.upper(), [])
        sub = history[-window:] if len(history) > window else history
        valid_days = len(sub)

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