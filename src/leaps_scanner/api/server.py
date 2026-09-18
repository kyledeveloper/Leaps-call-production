"""
Lightweight, zero-external-dependency HTTP API server and application state manager.
Provides endpoints for scanning, multi-board querying, instant in-memory alpha reranking,
and serves the responsive web dashboard.
Adheres strictly to Global Invariant 6 (Zero network re-fetch on alpha adjustment).
"""
import json
import logging
import math
import os
import threading
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from src.leaps_scanner.data.webull import WebullClient
from src.leaps_scanner.data.public_delayed import PublicDelayedClient
from src.leaps_scanner.data.store.iv_history import IVHistoryStore, default_iv_history_path
from src.leaps_scanner.data.store.daily_bars import DailyBarCache, default_daily_bar_cache_path
from src.leaps_scanner.scoring.ranker import MemoryRanker, RankedItem, StrategyCandidate
from src.leaps_scanner.scoring.csp_ranker import CSPCandidate, CSPFilterConfig, rank_csp_boards, CSPBoardSnapshot
import time
from src.leaps_scanner.data.rebalancer import get_universe_manager, DynamicUniverseManager, TICKER_REGEX
from src.leaps_scanner.data.universe import SymbologyNormalizer
from src.leaps_scanner.core.metrics import calculate_pexec, calculate_carry_cost, calculate_effective_leverage
from src.leaps_scanner.strategies.deep_itm import evaluate_deep_itm
from src.leaps_scanner.strategies.vol_discount import (
    VolDiscountUnderlyingMetrics,
    evaluate_vol_discount_underlying,
    evaluate_vol_discount_contract,
)
from src.leaps_scanner.strategies.oversold import (
    OversoldUnderlyingMetrics,
    evaluate_oversold_underlying,
    evaluate_oversold_contract,
)
from src.leaps_scanner.strategies.csp_harvest import evaluate_csp_harvest
from src.leaps_scanner.strategies.csp_wheel import evaluate_csp_wheel
from src.leaps_scanner.strategies.csp_vol_rank import evaluate_csp_vol_rank

logger = logging.getLogger(__name__)

# Single-ticker chain diagnostics: refresh admission control.
_TICKER_REFRESH_COOLDOWN_S = 15.0          # per-ticker cooldown between upstream refreshes
_FORCE_REFRESH_WINDOW_S = 60.0             # sliding window for the global refresh limiter
_FORCE_REFRESH_MAX_PER_WINDOW = 30         # max admitted force_refresh attempts per window
_FORCE_REFRESH_MAX_COOLDOWN_ENTRIES = 512  # hard cap on the per-ticker cooldown map


def calculate_execution_price(bid: float, ask: float, alpha: float = 0.5) -> float:
    try:
        if bid >= 0 and ask > 0 and bid <= ask:
            return calculate_pexec(bid=bid, ask=ask, alpha=alpha).p_exec
    except Exception:
        pass
    return round(bid + alpha * (ask - bid), 2) if (ask >= bid and bid >= 0) else max(ask, 0.0)



DEFAULT_SCAN_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]
CANONICAL_SCAN_TIERS = ("etfs", "djia", "sp100", "ndx", "core", "watchlist")
SCAN_TIER_ALIASES: Dict[str, str] = {
    "nasdaq100": "ndx",
    "npx": "ndx",
    "oex": "sp100",
    "watch": "watchlist",
}
SCAN_TIERS = CANONICAL_SCAN_TIERS + tuple(SCAN_TIER_ALIASES.keys())


def normalize_scan_tier(tier: Optional[str]) -> Optional[str]:
    if not tier:
        return None
    t = tier.strip().lower()
    return SCAN_TIER_ALIASES.get(t, t)


ENV_PATH = Path(__file__).resolve().parents[3] / ".env"



def _load_dotenv(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def _forget_env_secrets() -> None:
    for k in ("WEBULL_APP_KEY", "WEBULL_APP_SECRET"):
        os.environ.pop(k, None)


class AppState:
    """
    Central application state holding current cache of LEAPS candidates, ranker, and universe manager.
    """
    def __init__(
        self,
        offline_mode: bool = False,
        iv_store: Optional[IVHistoryStore] = None,
        bar_cache: Optional[DailyBarCache] = None,
        universe_manager: Optional[DynamicUniverseManager] = None,
    ):
        _load_dotenv()

        if iv_store is not None:
            self.iv_store = iv_store
        else:
            self.iv_store = IVHistoryStore(persist_path=str(default_iv_history_path()))

        if bar_cache is not None:
            self.bar_cache = bar_cache
        else:
            self.bar_cache = DailyBarCache(persist_path=str(default_daily_bar_cache_path()))

        if universe_manager is not None:
            self.universe_manager = universe_manager
        else:
            self.universe_manager = get_universe_manager(offline_mode=True)

        self.offline_mode = offline_mode
        if offline_mode:
            _forget_env_secrets()
            self.client = WebullClient(
                offline_mode=True,
                token_file=None,
            )
            self.source = "sandbox"
            self.connection_status = "sandbox"
        else:
            _forget_env_secrets()
            self.client = PublicDelayedClient(iv_store=self.iv_store, bar_cache=self.bar_cache)
            self.source = "delayed"
            self.connection_status = "delayed"

        self.candidates: List[StrategyCandidate] = []
        self.csp_candidates: List[CSPCandidate] = []
        self.ranker: Optional[MemoryRanker] = None

        self.last_scan_time: Optional[str] = None
        self.current_alpha: float = 0.5
        self.csp_snapshot: Optional[CSPBoardSnapshot] = None
        self.last_error: Optional[str] = None
        self.scan_tier: str = "etfs"
        self.scan_family: str = "leaps"
        self.scan_status: str = "idle"
        self.scan_progress: Dict[str, Any] = {"done": 0, "total": 0, "symbol": None}
        self.scanned_symbols: List[str] = []
        self._scan_cancel = False
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_seq: int = 0
        self._lock = threading.RLock()
        self._ticker_refresh_cooldown: Dict[str, float] = {}
        # Item 4: global force_refresh admission control (sliding window of admitted attempts).
        # Tunable per-instance for tests; defaults mirror the module constants above.
        self._force_refresh_times: Deque[float] = deque()
        self._force_refresh_window_s: float = _FORCE_REFRESH_WINDOW_S
        self._force_refresh_max_per_window: int = _FORCE_REFRESH_MAX_PER_WINDOW
        self._force_refresh_max_cooldown_entries: int = _FORCE_REFRESH_MAX_COOLDOWN_ENTRIES

    def resolve_scan_symbols(self, tier: Optional[str] = None) -> List[str]:
        raw = (tier or self.scan_tier or "etfs").strip().lower()
        if raw not in SCAN_TIERS:
            raw = "etfs"
        chosen = normalize_scan_tier(raw) or "etfs"
        if chosen == "etfs":
            names = self.universe_manager.get_constituents("etfs")
        elif chosen == "djia":
            names = self.universe_manager.get_constituents("djia")
        elif chosen == "sp100":
            names = self.universe_manager.get_constituents("sp100")
        elif chosen == "ndx":
            names = self.universe_manager.get_constituents("nasdaq100")
        elif chosen == "watchlist":
            names = self.universe_manager.get_constituents("watchlist")
            # Clause 5: Explicit Empty Watchlist Guard - do NOT fallback to DEFAULT_SCAN_SYMBOLS
            return [SymbologyNormalizer.to_canonical(s) for s in names]
        else:
            names = sorted(self.universe_manager.get_master_universe())
        names = [SymbologyNormalizer.to_canonical(s) for s in names]
        return names or list(DEFAULT_SCAN_SYMBOLS)

    def run_scan(self, symbols: Optional[List[str]] = None, tier: Optional[str] = None, family: str = "leaps") -> int:
        """Synchronous scan used by tests and sandbox mode switches."""
        if tier:
            normalized = normalize_scan_tier(tier)
            self.scan_tier = normalized or "etfs"
        with self._lock:
            self._scan_seq += 1
            seq = self._scan_seq
            self._scan_cancel = False
            self.scan_family = family
        syms = [SymbologyNormalizer.to_canonical(s) for s in symbols] if symbols else self.resolve_scan_symbols()
        if family == "csp":
            self._scan_csp_worker(syms, seq)
            return len(self.csp_candidates)
        else:
            self._scan_worker(syms, seq)
            return len(self.candidates)

    def request_scan(
        self,
        symbols: Optional[List[str]] = None,
        tier: Optional[str] = None,
        family: str = "leaps",
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Scan the selected universe tier for the requested strategy family ('leaps' or 'csp').
        Sandbox runs inline. Delayed/Webull run in the background.
        Switching tiers cancels the in-flight scan and starts the new one.
        Repeating the same tier and family while already running returns 409.
        """
        target_tier = None
        if tier:
            raw = tier.strip().lower()
            if raw not in SCAN_TIERS:
                return 400, {**self.public_config(), "error": "invalid_tier", "message": "tier must be etfs, djia, sp100, ndx, core, or watchlist"}
            target_tier = normalize_scan_tier(raw) or "etfs"

        eff_tier = target_tier or self.scan_tier or "etfs"
        syms = [SymbologyNormalizer.to_canonical(s) for s in symbols] if symbols else self.resolve_scan_symbols(eff_tier)

        # Clause 5: Empty Watchlist Guard - reject scan if watchlist is empty.
        # Still commit the tier (and cancel any in-flight job) so the UI stays
        # on the watchlist panel instead of snapping back to the previous universe.
        if eff_tier == "watchlist" and not syms:
            with self._lock:
                if target_tier:
                    self.scan_tier = target_tier
                if self.scan_status == "running":
                    self._scan_seq += 1
                    self._scan_cancel = True
                    self.scan_status = "idle"
                    self.scan_progress = {"done": 0, "total": 0, "symbol": None}
            return 400, {
                **self.public_config(),
                "error": "empty_watchlist",
                "message": "Watchlist is empty. Please add at least one ticker before scanning."
            }

        with self._lock:
            same_tier = target_tier is None or target_tier == self.scan_tier
            same_family = getattr(self, "scan_family", "leaps") == family
            same_symbols = (set(syms) == set(getattr(self, "scanned_symbols", [])))
            if self.scan_status == "running" and same_tier and same_family:
                if (target_tier or self.scan_tier) == "watchlist" and not same_symbols:
                    # Clause 6: Watchlist changed during scan - interrupt and restart
                    self._scan_cancel = True
                else:
                    return 409, {**self.public_config(), "error": "scan_in_progress", "message": f"A {family.upper()} scan is already running."}
            elif self.scan_status == "running" and (not same_tier or not same_family):
                # Switching tiers or families cancels the in-flight job
                self._scan_cancel = True
            self._scan_seq += 1
            seq = self._scan_seq
            self.scan_family = family
            if target_tier:
                self.scan_tier = target_tier
            self.scanned_symbols = list(syms)
            self.scan_status = "running"
            self.scan_progress = {"done": 0, "total": len(syms), "symbol": None}
            self._scan_cancel = False
            if family == "csp":
                self.csp_candidates = []
                self.csp_snapshot = None
            else:
                self.candidates = []
                self.ranker = None
            async_scan = self.source in ("delayed", "webull")

        worker_fn = self._scan_csp_worker if family == "csp" else self._scan_worker
        if async_scan:
            self._scan_thread = threading.Thread(target=worker_fn, args=(syms, seq), daemon=True)
            self._scan_thread.start()
            payload = self.public_config()
            payload["message"] = f"Scanning {self.scan_tier} ({len(syms)} names) for {family.upper()} in the background."
            return 202, payload
        worker_fn(syms, seq)
        payload = self.public_config()
        payload["message"] = f"Scanned {self.scan_tier} ({len(self.scanned_symbols)} names) for {family.upper()}."
        return 200, payload

    def cancel_scan(self) -> None:
        with self._lock:
            self._scan_seq += 1
            self._scan_cancel = True
            self.scan_status = "idle"
            self.scan_progress = {"done": 0, "total": 0, "symbol": None}

    def _scan_worker(self, symbols: List[str], seq: Optional[int] = None) -> None:
        collected: List[StrategyCandidate] = []
        total = len(symbols)

        def should_stop() -> bool:
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return True
                return bool(self._scan_cancel)

        def on_symbol_done(done: int, total_n: int, sym: str, batch: List[StrategyCandidate]) -> None:
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return
                if self._scan_cancel:
                    self.scan_status = "idle"
                    return
                collected.extend(batch)
                self.candidates = list(collected)
                self.ranker = MemoryRanker(self.candidates)
                self.last_scan_time = datetime.now(timezone.utc).isoformat()
                self.scan_progress = {"done": done, "total": total_n, "symbol": sym}
                self.scanned_symbols = list(symbols)

        if self.source == "delayed":
            scan_err = None
            try:
                self.client.get_leaps_candidates(
                    symbols,
                    progress_cb=on_symbol_done,
                    should_stop=should_stop,
                )
            except Exception as exc:
                scan_err = exc
                logger.exception("Delayed scan worker error: %s", exc)
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return
                if self._scan_cancel:
                    self.scan_status = "idle"
                    return
                if scan_err is not None:
                    self.last_error = f"scan_failed: {scan_err}"
                    self.scan_status = "error"
                    return
                asof_day = datetime.now(timezone.utc).date().isoformat()
                try:
                    self.iv_store.ingest_from_candidates(collected, asof_day)
                except OSError as exc:
                    logger.warning("IV ingest failed: %s", exc)
                self.scan_status = "done"
            return

        for i, sym in enumerate(symbols):
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return
                if self._scan_cancel:
                    self.scan_status = "idle"
                    return
                self.scan_status = "running"
                self.scan_progress = {"done": i, "total": total, "symbol": sym}
            try:
                batch = self.client.get_leaps_candidates([sym])
            except Exception as exc:
                logger.warning("Scan failed for %s: %s", sym, exc)
                batch = []
            collected.extend(batch)
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return
                self.candidates = list(collected)
                self.ranker = MemoryRanker(self.candidates)
                self.last_scan_time = datetime.now(timezone.utc).isoformat()
                self.scan_progress = {"done": i + 1, "total": total, "symbol": sym}
                self.scanned_symbols = list(symbols)
        with self._lock:
            if seq is not None and seq != self._scan_seq:
                return
            if self.source in ("delayed", "webull"):
                asof_day = datetime.now(timezone.utc).date().isoformat()
                try:
                    self.iv_store.ingest_from_candidates(collected, asof_day)
                except OSError as exc:
                    logger.warning("IV ingest failed: %s", exc)
            self.scan_status = "done"

    def _scan_csp_worker(self, symbols: List[str], seq: Optional[int] = None) -> None:
        collected: List[CSPCandidate] = []
        total = len(symbols)

        def should_stop() -> bool:
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return True
                return bool(self._scan_cancel)

        def on_symbol_done(done: int, total_n: int, sym: str, batch: List[CSPCandidate]) -> None:
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return
                if self._scan_cancel:
                    self.scan_status = "idle"
                    return
                collected.extend(batch)
                self.csp_candidates = list(collected)
                self.last_scan_time = datetime.now(timezone.utc).isoformat()
                self.scan_progress = {"done": done, "total": total_n, "symbol": sym}
                self.scanned_symbols = list(symbols)

        if self.source == "delayed" and hasattr(self.client, "get_csp_candidates"):
            scan_err = None
            try:
                self.client.get_csp_candidates(
                    symbols,
                    progress_cb=on_symbol_done,
                    should_stop=should_stop,
                )
            except Exception as exc:
                scan_err = exc
                logger.exception("Delayed CSP scan worker error: %s", exc)
            with self._lock:
                if seq is not None and seq != self._scan_seq:
                    return
                if self._scan_cancel:
                    self.scan_status = "idle"
                    return
                if scan_err is not None:
                    self.last_error = f"scan_failed: {scan_err}"
                    self.scan_status = "error"
                    return
                asof_day = datetime.now(timezone.utc).date().isoformat()
                try:
                    self.iv_store.ingest_from_candidates(collected, asof_day)
                except OSError as exc:
                    logger.warning("CSP IV ingest failed: %s", exc)
                self.scan_status = "done"
            self.get_csp_boards(alpha=self.current_alpha)
            return

        # Concurrently query symbols using ThreadPoolExecutor
        workers = min(8, max(1, total))
        lock = threading.Lock()
        completed = 0

        def fetch_sym_csp(sym: str) -> Tuple[str, List[CSPCandidate]]:
            if should_stop():
                return sym, []
            try:
                cands = self.client.get_csp_candidates([sym]) if hasattr(self.client, "get_csp_candidates") else []
            except Exception as exc:
                logger.warning("CSP scan failed for %s: %s", sym, exc)
                cands = []
            return sym, cands

        if workers <= 1 or total <= 1:
            for i, sym in enumerate(symbols):
                if should_stop():
                    break
                s, batch = fetch_sym_csp(sym)
                on_symbol_done(i + 1, total, s, batch)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(fetch_sym_csp, s): s for s in symbols}
                for fut in as_completed(futures):
                    if should_stop():
                        for pending in futures:
                            pending.cancel()
                        break
                    try:
                        sym, batch = fut.result()
                    except Exception as exc:
                        sym = futures[fut]
                        logger.warning("CSP worker exception for %s: %s", sym, exc)
                        batch = []
                    with lock:
                        completed += 1
                        done_n = completed
                    on_symbol_done(done_n, total, sym, batch)

        with self._lock:
            if seq is not None and seq != self._scan_seq:
                return
            self.scan_status = "done"
        self.get_csp_boards(alpha=self.current_alpha)

    def get_boards(self, alpha: float = 0.5) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            self.current_alpha = alpha
            if self.ranker is None and self.source == "sandbox":
                self.run_scan()
            ranker = self.ranker
        if ranker is None:
            return {"deep_itm": [], "vol_discount": [], "oversold": []}
        raw_boards = ranker.rank_boards(alpha=alpha)
        result: Dict[str, List[Dict[str, Any]]] = {}
        for b_name, items in raw_boards.items():
            result[b_name] = [asdict(it) for it in items]
        return result

    def get_csp_boards(
        self,
        alpha: float = 0.5,
        config: Optional[CSPFilterConfig] = None,
        cash_pool: float = 50000.0
    ) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            self.current_alpha = alpha
            csp_cands = list(self.csp_candidates)

        if not csp_cands:
            # Sandbox can hydrate from fixtures. Delayed/Webull wait for the scan worker —
            # a live GET must not block the dashboard on Nasdaq/Yahoo.
            if self.source == "sandbox" and hasattr(self.client, "get_csp_candidates"):
                try:
                    csp_cands = self.client.get_csp_candidates(self.resolve_scan_symbols())
                except Exception as e:
                    logger.warning("Failed to query CSP candidates: %s", e)
                    csp_cands = []
            with self._lock:
                self.csp_candidates = list(csp_cands)

        raw_boards = rank_csp_boards(
            csp_cands,
            config=config,
            alpha=alpha,
            cash_pool=cash_pool
        )

        # DC-CSP-10: Assemble immutable snapshot and perform atomic pointer swap
        frozen_boards: Dict[str, Tuple[RankedCSPItem, ...]] = {}
        for b_name, items in raw_boards.items():
            clean_name = b_name.replace("csp_", "")
            frozen_boards[clean_name] = tuple(items)
            frozen_boards[f"csp_{clean_name}"] = tuple(items)

        new_snapshot = CSPBoardSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            alpha=alpha,
            cash_pool=cash_pool,
            candidates=tuple(csp_cands),
            boards=frozen_boards,
        )
        with self._lock:
            self.csp_snapshot = new_snapshot
            self.csp_candidates = list(csp_cands)

        result: Dict[str, List[Dict[str, Any]]] = {}
        for b_name, items in raw_boards.items():
            serialized = [asdict(it) for it in items]
            clean_name = b_name.replace("csp_", "")
            result[clean_name] = serialized
            result[f"csp_{clean_name}"] = serialized
        return result

    def _evaluate_single_leaps_diagnostics(self, c: StrategyCandidate, strat: str, alpha: float) -> Dict[str, Any]:
        p_exec = calculate_execution_price(c.bid, c.ask, alpha)
        if strat == "deep_itm":
            active_res = evaluate_deep_itm(
                spot=c.spot, strike=c.strike, dte=c.dte, p_exec=p_exec, delta=c.delta,
                dividend_yield=getattr(c, "dividend_yield", 0.0), iv=c.iv, bid=c.bid, ask=c.ask,
                open_interest=getattr(c, "open_interest", 0), volume=getattr(c, "volume", 0)
            )
        elif strat == "vol_discount":
            use_iv_path = c.iv is not None and getattr(c, "iv_percentile", None) is not None and (
                getattr(c, "iv_history_days", 0) >= 90 or getattr(c, "valid_history_days", 0) >= 90
            )
            vol_metrics = VolDiscountUnderlyingMetrics(
                symbol=c.underlying,
                spot=c.spot,
                pct_change_20d=getattr(c, "pct_change_20d", 0.0),
                drawdown_52w_high=getattr(c, "drawdown_52w_high", 0.0),
                current_atm_iv=c.iv if c.iv is not None else 0.0,
                hv_252=getattr(c, "hv_252", 0.0),
                iv_percentile=getattr(c, "iv_percentile", 0.0) or 0.0,
                iv_rank=getattr(c, "iv_rank", 0.5) or 0.5,
                iv_z_score=getattr(c, "iv_z_score", 0.0) or 0.0,
                valid_history_days=getattr(c, "iv_history_days", 0) or getattr(c, "valid_history_days", 0),
                is_degraded=not use_iv_path,
                hv_20=getattr(c, "hv_20", 0.0),
                hv_percentile=getattr(c, "hv_percentile", None),
                hv_z_score=getattr(c, "hv_z_score", 0.0),
            )
            vol_u_res = evaluate_vol_discount_underlying(vol_metrics)
            active_res = evaluate_vol_discount_contract(
                underlying_result=vol_u_res,
                spot=c.spot,
                strike=c.strike,
                dte=c.dte,
                p_exec=p_exec,
                delta=c.delta,
                liquidity_status=getattr(c, "liquidity_status", "PASS"),
                dividend_yield=getattr(c, "dividend_yield", 0.0),
                days_to_earnings=getattr(c, "days_to_earnings", None)
            )
        else:  # oversold
            oversold_u_metrics = OversoldUnderlyingMetrics(
                symbol=c.underlying,
                spot=c.spot,
                rsi_14=getattr(c, "rsi_14", 50.0),
                pct_to_200dma=getattr(c, "pct_to_200dma", 0.0),
                drawdown_52w_high=getattr(c, "drawdown_52w_high", 0.0),
                bounce_52w_low=getattr(c, "bounce_52w_low", 0.0),
                hv_20=getattr(c, "hv_20", 0.0),
                bar_count=getattr(c, "valid_history_days", 252),
                is_valid=getattr(c, "valid_history_days", 252) >= 200
            )
            oversold_u_res = evaluate_oversold_underlying(oversold_u_metrics)
            active_res = evaluate_oversold_contract(
                underlying_result=oversold_u_res,
                spot=c.spot,
                strike=c.strike,
                dte=c.dte,
                p_exec=p_exec,
                delta=c.delta,
                liquidity_status=getattr(c, "liquidity_status", "PASS"),
                dividend_yield=getattr(c, "dividend_yield", 0.0),
                days_to_earnings=getattr(c, "days_to_earnings", None)
            )

        raw_gates = active_res.gates if hasattr(active_res, "gates") else {}

        intrinsic_val = max(0.0, c.spot - c.strike)
        intrinsic_ratio = (intrinsic_val / p_exec) if p_exec > 0 else 0.0
        leverage = calculate_effective_leverage(delta=c.delta, spot=c.spot, p_exec=p_exec)
        carry_res = calculate_carry_cost(spot=c.spot, strike=c.strike, dte=c.dte, p_exec=p_exec, dividend_yield=getattr(c, "dividend_yield", 0.0))
        carry = carry_res.total_annualized_carry

        gate_diagnostics = []
        for g_name, g_val in raw_gates.items():
            val_str = g_val.value if hasattr(g_val, "value") else str(g_val)
            reason = "WITHIN_RULE" if val_str == "PASS" else "VIOLATION"
            rule_target = ""
            actual_disp = ""

            if g_name == "strike":
                rule_target = "65% - 85% Spot"
                actual_disp = f"${c.strike:.1f} ({(c.strike/c.spot)*100:.1f}%)" if c.spot > 0 else f"${c.strike:.1f}"
            elif g_name == "delta":
                rule_target = "0.70 - 0.85 (0.85-0.90 Watch)"
                actual_disp = f"{c.delta:.2f}"
            elif g_name == "intrinsic":
                rule_target = ">= 65% (>= 55% Watch)"
                actual_disp = f"{intrinsic_ratio*100:.1f}%"
            elif g_name == "leverage":
                rule_target = "2.5x - 4.5x"
                actual_disp = f"{leverage:.1f}x"
            elif g_name == "carry":
                rule_target = "<= 15%/yr (<= 25% Watch)"
                actual_disp = f"{carry*100:.2f}%/yr"
            elif g_name == "dte":
                rule_target = ">= 300d (>= 250d Watch)"
                actual_disp = f"{int(c.dte)}d"
            elif g_name == "theta":
                rule_target = "<= 0.08%/d"
                actual_disp = f"{getattr(active_res, 'theta_daily_pct', 0.0)*100:.3f}%/d"
                if c.iv is None:
                    reason = "IV_UNAVAILABLE_DEGRADED"
            elif g_name == "spread":
                rule_target = "<= 15% Spread"
                spread = c.ask - c.bid
                actual_disp = f"${c.bid:.2f} / ${c.ask:.2f} (${spread:.2f})"
            elif g_name == "oi":
                rule_target = ">= 100 contracts"
                actual_disp = f"{getattr(c, 'open_interest', 0)} OI"
            elif g_name == "volume":
                rule_target = ">= 5 volume"
                actual_disp = f"{getattr(c, 'volume', 0)} Vol"
            else:
                rule_target = "Strategy rule"
                actual_disp = "Evaluated"

            if val_str != "PASS" and reason == "VIOLATION":
                for r in getattr(active_res, "reasons", []):
                    if g_name.upper() in r or (g_name == "intrinsic" and "INTRINSIC" in r):
                        reason = r
                        break
                if reason == "VIOLATION":
                    reason = f"{g_name.upper()}_{val_str}"

            gate_diagnostics.append({
                "gate_name": g_name,
                "status": val_str,
                "actual_value": actual_disp,
                "target_rule": rule_target,
                "reason": reason
            })

        return {
            "symbol": c.symbol,
            "underlying": c.underlying,
            "strike": c.strike,
            "spot": c.spot,
            "dte": c.dte,
            "bid": c.bid,
            "ask": c.ask,
            "p_exec": p_exec,
            "delta": c.delta,
            "iv": c.iv,
            "open_interest": getattr(c, "open_interest", 0),
            "volume": getattr(c, "volume", 0),
            "status": active_res.status.value if hasattr(active_res.status, "value") else str(active_res.status),
            "reasons": getattr(active_res, "reasons", []),
            "effective_leverage": leverage,
            "carry_cost": carry,
            "intrinsic_ratio": intrinsic_ratio,
            "gate_diagnostics": gate_diagnostics
        }

    def _evaluate_single_csp_diagnostics(self, c: CSPCandidate, strat: str, alpha: float, cash_pool: float) -> Dict[str, Any]:
        p_exec = calculate_execution_price(c.bid, c.ask, alpha)
        s1 = evaluate_csp_harvest(c)
        s2 = evaluate_csp_wheel(c)
        s3 = evaluate_csp_vol_rank(c)

        active_res = s1 if strat == "csp_harvest" else (s2 if strat == "csp_wheel" else s3)
        raw_gates = getattr(active_res, "gates", {})

        gate_diagnostics = []
        for g_name, g_val in raw_gates.items():
            val_str = g_val.value if hasattr(g_val, "value") else str(g_val)
            reason = "WITHIN_RULE" if val_str == "PASS" else "VIOLATION"
            rule_target = ""
            actual_disp = ""

            if g_name == "dte":
                rule_target = "7 - 45 days"
                actual_disp = f"{int(c.dte)}d"
            elif g_name == "delta":
                rule_target = "-0.15 to -0.30"
                actual_disp = f"{c.delta:.2f}"
            elif g_name == "buffer":
                rule_target = ">= 3.0%"
                actual_disp = f"{((c.spot - c.strike)/c.spot)*100:.2f}%" if c.spot > 0 else "0.0%"
            elif g_name == "aroc":
                rule_target = ">= 12.0%/yr"
                actual_disp = f"{getattr(c, 'aroc', 0.0)*100:.1f}%/yr"
            elif g_name == "pop":
                rule_target = ">= 70%"
                actual_disp = f"{getattr(c, 'pop', 0.0)*100:.1f}%"
            elif g_name == "spread":
                rule_target = "<= 15% Spread"
                spread = c.ask - c.bid
                actual_disp = f"${c.bid:.2f} / ${c.ask:.2f} (${spread:.2f})"
            elif g_name == "oi":
                rule_target = ">= 100 contracts"
                actual_disp = f"{getattr(c, 'open_interest', 0)} OI"
            elif g_name == "volume":
                rule_target = ">= 5 volume"
                actual_disp = f"{getattr(c, 'volume', 0)} Vol"
            elif g_name == "ivr":
                rule_target = ">= 50%"
                actual_disp = f"{getattr(c, 'ivr', 0.0)*100:.1f}%"
                if getattr(c, "ivr", None) is None:
                    reason = "IVR_UNAVAILABLE_DEGRADED"
            elif g_name == "earnings":
                rule_target = "No earnings in DTE"
                actual_disp = str(getattr(c, "earnings_status", "CONFIRMED_CLEAR"))
                if "UNVERIFIED" in actual_disp:
                    reason = "EARNINGS_UNVERIFIED_DEGRADED"
            else:
                rule_target = "Strategy rule"
                actual_disp = "Evaluated"

            if val_str != "PASS" and reason == "VIOLATION":
                for r in getattr(active_res, "reasons", []):
                    if g_name.upper() in r:
                        reason = r
                        break
                if reason == "VIOLATION":
                    reason = f"{g_name.upper()}_{val_str}"

            gate_diagnostics.append({
                "gate_name": g_name,
                "status": val_str,
                "actual_value": actual_disp,
                "target_rule": rule_target,
                "reason": reason
            })

        return {
            "symbol": c.symbol,
            "underlying": c.underlying,
            "strike": c.strike,
            "spot": c.spot,
            "dte": c.dte,
            "bid": c.bid,
            "ask": c.ask,
            "p_exec": p_exec,
            "delta": c.delta,
            "aroc": getattr(c, "aroc", 0.0),
            "buffer": getattr(c, "buffer", 0.0),
            "pop": getattr(c, "pop", 0.0),
            "status": active_res.status.value if hasattr(active_res.status, "value") else str(active_res.status),
            "reasons": getattr(active_res, "reasons", []),
            "gate_diagnostics": gate_diagnostics
        }

    def get_ticker_chain_diagnostics(
        self,
        ticker: Optional[str],
        family: str = "leaps",
        strategy: Optional[str] = None,
        alpha: float = 0.5,
        cash_pool: float = 50000.0,
        force_refresh: bool = False,
    ) -> Tuple[int, Dict[str, Any]]:
        if not ticker:
            return 400, {"error": "missing_ticker", "message": "Query parameter 'ticker' is required."}

        norm_ticker = SymbologyNormalizer.to_canonical(ticker)
        if not norm_ticker:
            return 400, {"error": "invalid_ticker", "message": f"Unable to canonicalize ticker '{ticker}'."}
        # Item 1: strict format gate — same TICKER_REGEX as the watchlist paths.
        # Blocks CRLF/header-injection probes and reflected-payload tickers before
        # the value reaches upstream clients or JSON echoes.
        if not TICKER_REGEX.match(norm_ticker):
            return 400, {"error": "invalid_ticker", "message": "Ticker format invalid; expected 1-5 letters with optional .XX suffix."}

        fam = (family or "leaps").strip().lower()
        if fam in ("cc", "covered_call"):
            # Clause 7: Covered Call blueprint protocol
            return 200, {
                "status": "blueprint",
                "family": "cc",
                "ticker": norm_ticker,
                "metrics_spec": [
                    {"name": "otm_call_yield", "label": "Call Premium Yield (30-60 DTE)", "target": ">= 1.5% / month"},
                    {"name": "annualized_aroc", "label": "Annualized Return on Capital", "target": ">= 15% / yr"},
                    {"name": "downside_buffer", "label": "Downside Cushion / Breakeven", "target": "Strike - Premium"},
                    {"name": "delta_window", "label": "Short Call Delta Window", "target": "0.20 to 0.35 Delta"}
                ],
                "message": "Covered Call quantitative screening engine blueprint. Full calculation coming soon."
            }

        # Clause 3: 15-second per-ticker rate-limit cooldown.
        # Item 4: plus a global sliding-window admission limiter (force_refresh
        # triggers upstream network I/O; the per-ticker cooldown is trivially
        # bypassed by rotating tickers) and a hard cap on the cooldown map.
        # Follow-up: the global quota is consumed only when a fetch will
        # actually occur — per-ticker-cooldown-blocked and offline-mode
        # requests are served from cache (zero upstream I/O).
        now = time.time()
        cooldown_active = False
        if force_refresh:
            with self._lock:
                # Bound the cooldown map: drop expired entries first (an entry
                # older than the cooldown window behaves exactly like a miss),
                # then hard-cap as a backstop against clock skew / bursts.
                cd = self._ticker_refresh_cooldown
                for k in [k for k, ts in cd.items() if now - ts >= _TICKER_REFRESH_COOLDOWN_S]:
                    del cd[k]
                last_refresh = cd.get(norm_ticker, 0.0)
                if now - last_refresh < _TICKER_REFRESH_COOLDOWN_S:
                    cooldown_active = True
                    force_refresh = False
                else:
                    if not self.offline_mode:
                        admitted = self._force_refresh_times
                        cutoff = now - self._force_refresh_window_s
                        while admitted and admitted[0] <= cutoff:
                            admitted.popleft()
                        if len(admitted) >= self._force_refresh_max_per_window:
                            retry_in = int(admitted[0] + self._force_refresh_window_s - now) + 1
                            return 429, {
                                "error": "rate_limited",
                                "message": f"Too many chain refreshes; try again in {retry_in}s.",
                                "retry_after_s": retry_in,
                            }
                        admitted.append(now)
                    cd[norm_ticker] = now
                    while len(cd) > self._force_refresh_max_cooldown_entries:
                        # Evict by oldest timestamp, not insertion order: an
                        # existing key updated in place keeps its original
                        # position with a fresh timestamp.
                        cd.pop(min(cd, key=lambda k: cd[k]))

        # Normalize strategy key (Clause 5: prefix normalization)
        strat = (strategy or "").strip().lower()
        if fam == "csp":
            if strat in ("harvest", "csp_harvest"):
                strat = "csp_harvest"
            elif strat in ("wheel", "csp_wheel"):
                strat = "csp_wheel"
            elif strat in ("vol_rank", "csp_vol_rank"):
                strat = "csp_vol_rank"
            else:
                strat = "csp_harvest"
        else:
            if strat not in ("deep_itm", "vol_discount", "oversold"):
                strat = "deep_itm"

        warning = None
        contracts_out = []
        spot_price = 0.0

        if fam == "csp":
            matches = [c for c in self.csp_candidates if (c.underlying or "").upper() == norm_ticker]
            if (not matches or force_refresh) and not self.offline_mode:
                try:
                    new_cands = self.client.get_csp_candidates([norm_ticker])
                    if new_cands:
                        with self._lock:
                            # Clause 1: Atomic upsert, never wipe other tickers
                            self.csp_candidates = [c for c in self.csp_candidates if (c.underlying or "").upper() != norm_ticker] + new_cands
                            matches = new_cands
                except Exception as exc:
                    logger.warning("Single-ticker CSP live fetch failed for %s: %s", norm_ticker, exc)
                    warning = f"upstream_refresh_failed: {exc}"

            for c in matches:
                spot_price = c.spot if spot_price == 0.0 else spot_price
                diag_item = self._evaluate_single_csp_diagnostics(c, strat, alpha, cash_pool)
                contracts_out.append(diag_item)

        else: # leaps
            matches = [c for c in self.candidates if (c.underlying or "").upper() == norm_ticker]
            if (not matches or force_refresh) and not self.offline_mode:
                try:
                    new_cands = self.client.get_leaps_candidates([norm_ticker])
                    if new_cands:
                        with self._lock:
                            # Clause 1: Atomic upsert, never wipe other tickers
                            self.candidates = [c for c in self.candidates if (c.underlying or "").upper() != norm_ticker] + new_cands
                            self.ranker = MemoryRanker(self.candidates)
                            matches = new_cands
                except Exception as exc:
                    logger.warning("Single-ticker LEAPS live fetch failed for %s: %s", norm_ticker, exc)
                    warning = f"upstream_refresh_failed: {exc}"

            for c in matches:
                spot_price = c.spot if spot_price == 0.0 else spot_price
                diag_item = self._evaluate_single_leaps_diagnostics(c, strat, alpha)
                contracts_out.append(diag_item)

        payload = {
            "status": "ok",
            "ticker": norm_ticker,
            "spot": spot_price,
            "family": fam,
            "strategy": strat,
            "alpha": alpha,
            "cash_pool": cash_pool,
            "cooldown_active": cooldown_active,
            "contract_count": len(contracts_out),
            "contracts": contracts_out,
            "asof": self.last_scan_time or datetime.now(timezone.utc).isoformat()
        }
        if warning:
            payload["warning"] = warning
        return 200, payload

    def _has_credentials(self) -> bool:
        if self.source != "webull":
            return False
        key = (getattr(self.client, "app_key", None) or "") if self.client else ""
        secret = (getattr(self.client, "app_secret", None) or "") if self.client else ""
        return bool(str(key).strip() and str(secret).strip()) and not self.offline_mode

    def _credential_hint(self) -> Optional[str]:
        if self.source != "webull":
            return None
        key = (getattr(self.client, "app_key", None) or "") if self.client else ""
        if self.offline_mode or len(str(key)) < 8:
            return None
        return f"{key[:4]}…{key[-4:]}"

    def public_config(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "source": self.source,
            "offline_mode": self.offline_mode,
            "connection_status": self.connection_status,
            "has_credentials": self._has_credentials(),
            "credential_hint": self._credential_hint(),
            "secrets_persisted": False,
            "last_error": self.last_error,
            "last_scan_time": self.last_scan_time,
            "candidate_count": len(self.candidates),
            "csp_candidate_count": len(self.csp_candidates),
            "current_alpha": self.current_alpha,
            "scan_tier": self.scan_tier,
            "scan_family": getattr(self, "scan_family", "leaps"),
            "scan_status": self.scan_status,
            "scan_progress": dict(self.scan_progress),
            "scan_symbol_count": len(self.scanned_symbols),
            "universe": {
                "etfs": len(self.universe_manager.get_constituents("etfs")),
                "djia": len(self.universe_manager.get_constituents("djia")),
                "sp100": len(self.universe_manager.get_constituents("sp100")),
                "ndx": len(self.universe_manager.get_constituents("nasdaq100")),
                "core": len(self.universe_manager.get_master_universe()),
                "watchlist": len(self.universe_manager.get_constituents("watchlist")),
            },
            "watchlist_symbols": self.universe_manager.get_constituents("watchlist"),
            "strategies": ["deep_itm", "vol_discount", "oversold"],
            "csp_strategies": ["harvest", "wheel", "vol_rank"],
        }


    def set_mode(
        self,
        offline: bool = True,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        source: Optional[str] = None,
        family: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Switch sandbox / delayed public / live Webull.
        Returns (http_status, payload). Never echoes secrets.
        """
        if source is None:
            source = "sandbox" if offline else "webull"
        source = str(source).strip().lower()
        if source not in ("sandbox", "delayed", "webull"):
            return 400, {**self.public_config(), "error": "invalid_source", "message": "source must be sandbox, delayed, or webull"}

        fam = str(family or self.scan_family or "leaps").strip().lower()
        if fam not in ("leaps", "csp"):
            fam = "leaps"

        with self._lock:
            self._scan_seq += 1
            self._scan_cancel = True
            self.scan_status = "idle"
            self.scan_progress = {"done": 0, "total": 0, "symbol": None}
            self.scanned_symbols = []
            self.candidates = []
            self.ranker = None
            self.csp_candidates = []
            self.csp_snapshot = None
        thread = self._scan_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        scan_after: Optional[str] = None
        with self._lock:
            if source == "sandbox":
                _forget_env_secrets()
                os.environ["WEBULL_OFFLINE_MODE"] = "true"
                self.client = WebullClient(offline_mode=True, token_file=None)
                self.universe_manager = get_universe_manager(offline_mode=True)
                self.offline_mode = True
                self.source = "sandbox"
                self.connection_status = "sandbox"
                self.last_error = None
                scan_after = "sync"

            elif source == "delayed":
                _forget_env_secrets()
                self.client = PublicDelayedClient(iv_store=self.iv_store, bar_cache=self.bar_cache)
                self.universe_manager = get_universe_manager(offline_mode=True)
                self.offline_mode = False
                self.source = "delayed"
                self.connection_status = "delayed"
                self.last_error = None
                scan_after = "async"

            else:
                key = (app_key or (getattr(self.client, "app_key", None) if self.client and not getattr(self.client, "offline_mode", True) else None) or "").strip()
                secret = (app_secret or (getattr(self.client, "app_secret", None) if self.client and not getattr(self.client, "offline_mode", True) else None) or "").strip()
                if not key or not secret:
                    self.last_error = "missing_credentials"
                    return 400, {
                        **self.public_config(),
                        "error": "missing_credentials",
                        "message": (
                            "Live Webull data needs an OpenAPI App Key and App Secret. "
                            "They stay in this running process only — not written to disk, not returned by the API. "
                            "Or use Delayed public (no keys)."
                        ),
                    }
                self.connection_status = "connecting"
                self.last_error = None
                try:
                    live_client = WebullClient(
                        app_key=key,
                        app_secret=secret,
                        offline_mode=False,
                        token_file=None,
                    )
                    live_client.auth.get_token()
                except Exception as exc:
                    _forget_env_secrets()
                    self.connection_status = "error"
                    self.offline_mode = False
                    self.source = "delayed"
                    self.client = PublicDelayedClient(iv_store=self.iv_store, bar_cache=self.bar_cache)
                    self.universe_manager = get_universe_manager(offline_mode=True)
                    scan_after = "async"
                    self._pending_auth_error = (
                        "Webull login failed. Falling back to Delayed public. "
                        f"Detail: {type(exc).__name__}"
                    )
                else:
                    _forget_env_secrets()
                    self.client = live_client
                    self.universe_manager = get_universe_manager(offline_mode=False)
                    self.offline_mode = False
                    self.source = "webull"
                    self.connection_status = "live"
                    scan_after = "async"

        if getattr(self, "_pending_auth_error", None) and self.last_error == "auth_failed":
            self.run_scan()
            msg = self._pending_auth_error
            self._pending_auth_error = None
            return 502, {**self.public_config(), "error": "auth_failed", "message": msg}

        if scan_after == "sync":
            count = self.run_scan(family=fam)
            payload = self.public_config()
            payload["message"] = "Switched to sandbox mock data."
            payload["candidate_count"] = count
            return 200, payload

        code, payload = self.request_scan(family=fam)
        if self.source == "delayed":
            payload["message"] = (
                "Delayed public: Yahoo history + Nasdaq OPRA delayed chain. "
                f"Scanning {self.scan_tier} ({payload.get('scan_symbol_count')} names). "
                "~15 min delay, not NBBO."
            )
        else:
            payload["message"] = "Live Webull connected. Scan started on the selected universe."
        return code, payload


def create_api_handler_class(state: AppState):
    """
    Factory creating a testable and runnable HTTP request handler.
    """
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    class APIHandler(BaseHTTPRequestHandler):
        @classmethod
        def dispatch(cls, method: str, path: str, body: bytes) -> Tuple[int, Dict[str, str], bytes]:
            parsed = urllib.parse.urlparse(path)
            query_params = urllib.parse.parse_qs(parsed.query)
            clean_path = parsed.path

            headers = {"Content-Type": "application/json"}

            # GET / or /index.html or /csp or /csp.html
            if method in ("GET", "HEAD") and clean_path in ("/", "/index.html", "/csp", "/csp.html"):
                index_path = os.path.join(static_dir, "index.html")
                if os.path.exists(index_path):
                    with open(index_path, "rb") as f:
                        content = f.read()
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, content if method == "GET" else b""
                else:
                    html = b"<h1>LEAPS Scanner API</h1>"
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, html if method == "GET" else b""

            # GET /workflow or /workflow.html or /csp-workflow.html
            if method in ("GET", "HEAD") and clean_path in ("/workflow", "/workflow.html", "/csp-workflow.html", "/docs/csp-workflow-zh.html"):
                wf_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "csp-workflow-zh.html"))
                if os.path.exists(wf_path):
                    with open(wf_path, "rb") as f:
                        content = f.read()
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, content if method == "GET" else b""

            # GET /chain or /chain.html
            if method in ("GET", "HEAD") and clean_path in ("/chain", "/chain.html"):
                chain_path = os.path.join(static_dir, "chain.html")
                if os.path.exists(chain_path):
                    with open(chain_path, "rb") as f:
                        content = f.read()
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, content if method == "GET" else b""
                else:
                    html = b"<!DOCTYPE html><html><head><title>Option Chain Diagnostics</title></head><body><h1>Option Chain Diagnostics</h1><p>Loading chain view...</p></body></html>"
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, html if method == "GET" else b""

            # GET /chain-workflow or /chain-workflow.html or /docs/chain-workflow-zh.html
            if method in ("GET", "HEAD") and clean_path in ("/chain-workflow", "/chain-workflow.html", "/docs/chain-workflow-zh.html"):
                wf_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "chain-workflow-zh.html"))
                if os.path.exists(wf_path):
                    with open(wf_path, "rb") as f:
                        content = f.read()
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, content if method == "GET" else b""

            # GET /api/v1/config
            if method in ("GET", "HEAD") and clean_path == "/api/v1/config":
                payload = json.dumps(state.public_config()).encode("utf-8")
                return 200, headers, payload if method == "GET" else b""

            # POST /api/v1/mode  {source?, offline?, app_key?, app_secret?}
            if method == "POST" and clean_path == "/api/v1/mode":
                req_data: Dict[str, Any] = {}
                if body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                    except Exception:
                        req_data = {}
                source = req_data.get("source")
                if isinstance(source, str):
                    source = source.strip() or None
                else:
                    source = None
                family = req_data.get("family")
                if isinstance(family, str):
                    family = family.strip() or None
                else:
                    family = None
                offline = bool(req_data.get("offline", True))
                app_key = req_data.get("app_key")
                app_secret = req_data.get("app_secret")
                if isinstance(app_key, str):
                    app_key = app_key.strip() or None
                else:
                    app_key = None
                if isinstance(app_secret, str):
                    app_secret = app_secret.strip() or None
                else:
                    app_secret = None
                code, payload = state.set_mode(
                    offline=offline,
                    app_key=app_key,
                    app_secret=app_secret,
                    source=source,
                    family=family,
                )
                return code, headers, json.dumps(payload).encode("utf-8")

            # POST /api/v1/scan
            if method == "POST" and clean_path == "/api/v1/scan":
                symbols = None
                tier = None
                family = "leaps"
                if body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                        symbols = req_data.get("symbols")
                        tier = req_data.get("tier")
                        family = req_data.get("family") or "leaps"
                    except Exception:
                        pass
                if isinstance(tier, str):
                    tier = tier.strip().lower() or None
                else:
                    tier = None
                if symbols is not None and not isinstance(symbols, list):
                    symbols = None
                code, payload = state.request_scan(symbols=symbols, tier=tier, family=family)
                return code, headers, json.dumps(payload).encode("utf-8")

            # GET /api/v1/boards
            if method == "GET" and clean_path == "/api/v1/boards":
                alpha = float(query_params.get("alpha", [state.current_alpha])[0])
                boards = state.get_boards(alpha=alpha)
                data = {
                    "alpha": alpha,
                    "boards": boards,
                    "offline_mode": state.offline_mode,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # POST /api/v1/rerank
            if method == "POST" and clean_path == "/api/v1/rerank":
                alpha = state.current_alpha
                if body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                        alpha = float(req_data.get("alpha", alpha))
                    except Exception:
                        pass
                boards = state.get_boards(alpha=alpha)
                data = {
                    "alpha": alpha,
                    "boards": boards,
                    "recalculation_mode": "IN_MEMORY_ZERO_NETWORK"
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # GET /api/v1/csp/boards or /api/csp/boards
            if method in ("GET", "HEAD") and clean_path in ("/api/v1/csp/boards", "/api/csp/boards"):
                alpha = float(query_params.get("alpha", [state.current_alpha])[0])
                cash_pool = float(query_params.get("cash_pool", [50000.0])[0])
                max_cap = query_params.get("max_capital", [None])[0]
                max_capital = float(max_cap) if max_cap else None

                cfg = CSPFilterConfig(
                    filter_aroc=query_params.get("filter_aroc", ["true"])[0].lower() == "true",
                    min_aroc=float(query_params.get("min_aroc", [0.12])[0]),
                    filter_buffer=query_params.get("filter_buffer", ["true"])[0].lower() == "true",
                    min_buffer=float(query_params.get("min_buffer", [0.03])[0]),
                    filter_ivr=query_params.get("filter_ivr", ["false"])[0].lower() == "true",
                    min_ivr=float(query_params.get("min_ivr", [0.50])[0]),
                    filter_pop=query_params.get("filter_pop", ["true"])[0].lower() == "true",
                    min_pop=float(query_params.get("min_pop", [0.70])[0]),
                    filter_earnings=query_params.get("filter_earnings", ["true"])[0].lower() == "true",
                    strict_earnings=query_params.get("strict_earnings", ["false"])[0].lower() == "true",
                    filter_liquidity=query_params.get("filter_liquidity", ["true"])[0].lower() == "true",
                    max_capital_per_contract=max_capital,
                )
                boards = state.get_csp_boards(alpha=alpha, config=cfg, cash_pool=cash_pool)
                data = {
                    "alpha": alpha,
                    "cash_pool": cash_pool,
                    "boards": boards,
                    "offline_mode": state.offline_mode,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # POST /api/v1/csp/rerank or /api/csp/rerank
            if method == "POST" and clean_path in ("/api/v1/csp/rerank", "/api/csp/rerank"):
                alpha = state.current_alpha
                cash_pool = 50000.0
                cfg = CSPFilterConfig()
                if body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                        alpha = float(req_data.get("alpha", alpha))
                        cash_pool = float(req_data.get("cash_pool", cash_pool))
                        c_dict = req_data.get("config", {})
                        if isinstance(c_dict, dict):
                            cfg = CSPFilterConfig(
                                filter_aroc=c_dict.get("filter_aroc", cfg.filter_aroc),
                                min_aroc=float(c_dict.get("min_aroc", cfg.min_aroc)),
                                filter_buffer=c_dict.get("filter_buffer", cfg.filter_buffer),
                                min_buffer=float(c_dict.get("min_buffer", cfg.min_buffer)),
                                filter_ivr=c_dict.get("filter_ivr", cfg.filter_ivr),
                                min_ivr=float(c_dict.get("min_ivr", cfg.min_ivr)),
                                filter_pop=c_dict.get("filter_pop", cfg.filter_pop),
                                min_pop=float(c_dict.get("min_pop", cfg.min_pop)),
                                filter_earnings=c_dict.get("filter_earnings", cfg.filter_earnings),
                                strict_earnings=c_dict.get("strict_earnings", cfg.strict_earnings),
                                filter_liquidity=c_dict.get("filter_liquidity", cfg.filter_liquidity),
                                max_capital_per_contract=c_dict.get("max_capital_per_contract"),
                            )
                    except Exception:
                        pass
                boards = state.get_csp_boards(alpha=alpha, config=cfg, cash_pool=cash_pool)
                data = {
                    "alpha": alpha,
                    "cash_pool": cash_pool,
                    "boards": boards,
                    "recalculation_mode": "IN_MEMORY_ZERO_NETWORK"
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # GET /api/v1/ticker/chain or /api/ticker/chain
            if method == "GET" and clean_path in ("/api/v1/ticker/chain", "/api/ticker/chain"):
                ticker = query_params.get("ticker", query_params.get("symbol", [None]))[0]
                if not ticker:
                    return 400, headers, json.dumps({"error": "missing_ticker", "message": "Missing 'ticker' parameter"}).encode("utf-8")
                family = query_params.get("family", ["leaps"])[0]
                strategy = query_params.get("strategy", [None])[0]
                force_refresh_str = query_params.get("force_refresh", ["false"])[0].lower()
                force_refresh = force_refresh_str in ("true", "1", "yes")

                alpha_str = query_params.get("alpha", [None])[0]
                try:
                    alpha = float(alpha_str) if alpha_str is not None else 0.5
                except ValueError:
                    alpha = 0.5
                # Item 3: float("nan")/"inf" parse fine but poison json.dumps (emits
                # non-standard NaN) and downstream math. Reject non-finite/out-of-range.
                if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
                    return 400, headers, json.dumps({
                        "error": "invalid_alpha",
                        "message": "Query parameter 'alpha' must be a finite number between 0 and 1.",
                    }).encode("utf-8")

                cash_str = query_params.get("cash_pool", [None])[0]
                try:
                    cash_pool = float(cash_str) if cash_str is not None else 50000.0
                except ValueError:
                    cash_pool = 50000.0
                if not math.isfinite(cash_pool) or cash_pool <= 0:
                    return 400, headers, json.dumps({
                        "error": "invalid_cash_pool",
                        "message": "Query parameter 'cash_pool' must be a finite number greater than 0.",
                    }).encode("utf-8")

                code, payload = state.get_ticker_chain_diagnostics(
                    ticker=ticker,
                    family=family,
                    strategy=strategy,
                    alpha=alpha,
                    cash_pool=cash_pool,
                    force_refresh=force_refresh,
                )
                return code, headers, json.dumps(payload).encode("utf-8")

            # GET /api/v1/watchlist
            if method == "GET" and clean_path in ("/api/v1/watchlist", "/api/watchlist"):
                watchlist = state.universe_manager.get_watchlist()
                data = {
                    "status": "ok",
                    "watchlist": watchlist,
                    "symbols": watchlist,
                    "count": len(watchlist)
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # POST /api/v1/watchlist
            if method == "POST" and clean_path in ("/api/v1/watchlist", "/api/watchlist"):
                action = "add"
                ticker = None
                tickers = None
                if body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                        action = req_data.get("action", "add")
                        ticker = req_data.get("ticker") or req_data.get("symbol")
                        tickers = req_data.get("tickers") or req_data.get("symbols")
                    except Exception:
                        pass
                if action == "set" and isinstance(tickers, list):
                    updated = state.universe_manager.set_watchlist(tickers)
                    return 200, headers, json.dumps({
                        "status": "ok",
                        "watchlist": updated,
                        "symbols": updated,
                        "count": len(updated)
                    }).encode("utf-8")
                elif action == "remove":
                    if not ticker:
                        return 400, headers, json.dumps({"error": "missing_ticker", "message": "Missing ticker parameter"}).encode("utf-8")
                    removed = state.universe_manager.remove_watchlist_ticker(ticker)
                    wl = state.universe_manager.get_watchlist()
                    return 200, headers, json.dumps({
                        "status": "ok",
                        "removed": removed,
                        "watchlist": wl,
                        "symbols": wl,
                        "count": len(wl)
                    }).encode("utf-8")
                else:  # add
                    if not ticker:
                        return 400, headers, json.dumps({"error": "missing_ticker", "message": "Missing ticker parameter"}).encode("utf-8")
                    ok, msg = state.universe_manager.add_watchlist_ticker(ticker)
                    wl = state.universe_manager.get_watchlist()
                    code = 200 if ok else 400
                    return code, headers, json.dumps({
                        "status": "ok" if ok else "error",
                        "message": msg,
                        "watchlist": wl,
                        "symbols": wl,
                        "count": len(wl)
                    }).encode("utf-8")

            # DELETE /api/v1/watchlist
            if method == "DELETE" and clean_path in ("/api/v1/watchlist", "/api/watchlist"):
                ticker = query_params.get("ticker", query_params.get("symbol", [None]))[0]
                if not ticker and body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                        ticker = req_data.get("ticker") or req_data.get("symbol")
                    except Exception:
                        pass
                if not ticker:
                    return 400, headers, json.dumps({"error": "missing_ticker", "message": "Missing ticker parameter"}).encode("utf-8")
                removed = state.universe_manager.remove_watchlist_ticker(ticker)
                wl = state.universe_manager.get_watchlist()
                return 200, headers, json.dumps({
                    "status": "ok",
                    "removed": removed,
                    "watchlist": wl,
                    "symbols": wl,
                    "count": len(wl)
                }).encode("utf-8")

            # GET /api/v1/universe
            if method == "GET" and clean_path in ("/api/v1/universe", "/api/universe"):
                data = {
                    "status": "healthy",
                    "indices": {
                        "sp100": len(state.universe_manager.get_constituents("sp100")),
                        "nasdaq100": len(state.universe_manager.get_constituents("nasdaq100")),
                        "ndx": len(state.universe_manager.get_constituents("nasdaq100")),
                        "djia": len(state.universe_manager.get_constituents("djia")),
                        "etfs": len(state.universe_manager.get_constituents("etfs")),
                        "adrs": len(state.universe_manager.get_constituents("adrs")),
                        "watchlist": len(state.universe_manager.get_constituents("watchlist")),
                    },
                    "master_count": len(state.universe_manager.get_master_universe()),
                    "rebalance_history": state.universe_manager.get_rebalance_history()[-10:],
                    "last_synced": getattr(state.universe_manager, "_last_synced", None)
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # POST /api/v1/universe/sync
            if method == "POST" and clean_path in ("/api/v1/universe/sync", "/api/universe/sync"):
                results = {}
                for idx in ["djia", "sp100", "nasdaq100"]:
                    results[idx] = state.universe_manager.sync_index(idx)
                data = {
                    "message": "Universe rebalance sync completed",
                    "results": results,
                    "master_count": len(state.universe_manager.get_master_universe()),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            return 404, headers, json.dumps({"error": "Not Found"}).encode("utf-8")

        def do_GET(self):
            code, headers, body = self.dispatch("GET", self.path, b"")
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            code, headers, body = self.dispatch("HEAD", self.path, b"")
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""
            code, headers, body_out = self.dispatch("POST", self.path, body)
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body_out)))
            self.end_headers()
            self.wfile.write(body_out)

        def do_DELETE(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""
            code, headers, body_out = self.dispatch("DELETE", self.path, body)
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body_out)))
            self.end_headers()
            self.wfile.write(body_out)

        def log_message(self, format, *args):
            # Suppress default server access logs during tests
            pass

    return APIHandler


def run_server(port: int = 8000, offline_mode: bool = False):
    """
    Launch HTTP server on specified port.
    """
    state = AppState(offline_mode=offline_mode)
    if offline_mode:
        state.run_scan()
    else:
        state.request_scan(tier="etfs", family="csp")
    handler_cls = create_api_handler_class(state)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    print(f"LEAPS/CSP Scanner server running at 0.0.0.0:{port} (Source: {state.source})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
