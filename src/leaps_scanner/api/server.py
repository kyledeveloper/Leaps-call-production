"""
Lightweight, zero-external-dependency HTTP API server and application state manager.
Provides endpoints for scanning, multi-board querying, instant in-memory alpha reranking,
and serves the responsive web dashboard.
Adheres strictly to Global Invariant 6 (Zero network re-fetch on alpha adjustment).
"""
import json
import logging
import os
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from src.leaps_scanner.data.webull import WebullClient
from src.leaps_scanner.data.public_delayed import PublicDelayedClient
from src.leaps_scanner.data.store.iv_history import IVHistoryStore, default_iv_history_path
from src.leaps_scanner.data.store.daily_bars import DailyBarCache, default_daily_bar_cache_path
from src.leaps_scanner.scoring.ranker import MemoryRanker, RankedItem, StrategyCandidate
from src.leaps_scanner.scoring.csp_ranker import CSPCandidate, CSPFilterConfig, rank_csp_boards, CSPBoardSnapshot
from src.leaps_scanner.data.rebalancer import get_universe_manager
from src.leaps_scanner.data.universe import SymbologyNormalizer

logger = logging.getLogger(__name__)



DEFAULT_SCAN_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]
CANONICAL_SCAN_TIERS = ("etfs", "djia", "sp100", "ndx", "core")
SCAN_TIER_ALIASES: Dict[str, str] = {
    "nasdaq100": "ndx",
    "npx": "ndx",
    "oex": "sp100",
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
    ):
        _load_dotenv()
        # Live only when explicitly requested AND credentials exist.
        if not offline_mode and not (
            (os.environ.get("WEBULL_APP_KEY") or "").strip()
            and (os.environ.get("WEBULL_APP_SECRET") or "").strip()
        ):
            offline_mode = True
        self.offline_mode = offline_mode
        if offline_mode:
            _forget_env_secrets()
        self.client = WebullClient(
            offline_mode=offline_mode,
            token_file=None,
        )
        self.universe_manager = get_universe_manager(offline_mode=offline_mode)

        if iv_store is not None:
            self.iv_store = iv_store
        else:
            self.iv_store = IVHistoryStore(persist_path=str(default_iv_history_path()))

        if bar_cache is not None:
            self.bar_cache = bar_cache
        else:
            self.bar_cache = DailyBarCache(persist_path=str(default_daily_bar_cache_path()))

        self.candidates: List[StrategyCandidate] = []
        self.csp_candidates: List[CSPCandidate] = []
        self.ranker: Optional[MemoryRanker] = None

        self.last_scan_time: Optional[str] = None
        self.current_alpha: float = 0.5
        self.csp_snapshot: Optional[CSPBoardSnapshot] = None
        self.last_error: Optional[str] = None
        self.source: str = "sandbox" if offline_mode else "webull"
        self.connection_status: str = "sandbox" if offline_mode else "live"
        self.scan_tier: str = "etfs"
        self.scan_family: str = "leaps"
        self.scan_status: str = "idle"
        self.scan_progress: Dict[str, Any] = {"done": 0, "total": 0, "symbol": None}
        self.scanned_symbols: List[str] = []
        self._scan_cancel = False
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_seq: int = 0
        self._lock = threading.RLock()

    def resolve_scan_symbols(self, tier: Optional[str] = None) -> List[str]:
        raw = (tier or self.scan_tier or "etfs").strip().lower()
        if raw not in SCAN_TIERS:
            raw = "etfs"
        chosen = normalize_scan_tier(raw) or "etfs"
        self.scan_tier = chosen
        if chosen == "etfs":
            names = self.universe_manager.get_constituents("etfs")
        elif chosen == "djia":
            names = self.universe_manager.get_constituents("djia")
        elif chosen == "sp100":
            names = self.universe_manager.get_constituents("sp100")
        elif chosen == "ndx":
            names = self.universe_manager.get_constituents("nasdaq100")
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
                return 400, {**self.public_config(), "error": "invalid_tier", "message": "tier must be etfs, djia, sp100, ndx, or core"}
            target_tier = normalize_scan_tier(raw) or "etfs"

        with self._lock:
            same_tier = target_tier is None or target_tier == self.scan_tier
            same_family = getattr(self, "scan_family", "leaps") == family
            if self.scan_status == "running" and same_tier and same_family:
                return 409, {**self.public_config(), "error": "scan_in_progress", "message": f"A {family.upper()} scan is already running."}
            self._scan_seq += 1
            seq = self._scan_seq
            self.scan_family = family
            if target_tier:
                self.scan_tier = target_tier
            syms = [SymbologyNormalizer.to_canonical(s) for s in symbols] if symbols else self.resolve_scan_symbols()
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
            ranker = self.ranker
        if ranker is None:
            self.run_scan()
            with self._lock:
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
            },
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
                    self.last_error = "auth_failed"
                    self.offline_mode = True
                    self.source = "sandbox"
                    self.client = WebullClient(offline_mode=True, token_file=None)
                    self.universe_manager = get_universe_manager(offline_mode=True)
                    scan_after = "sync"
                    self._pending_auth_error = (
                        "Webull login failed. Check App Key/Secret, then approve OpenAPI access "
                        "in the Webull app. Staying on sandbox until that succeeds. "
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
                        "adrs": len(state.universe_manager.get_constituents("adrs"))
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

        def log_message(self, format, *args):
            # Suppress default server access logs during tests
            pass

    return APIHandler


def run_server(port: int = 8000, offline_mode: bool = False):
    """
    Launch HTTP server on specified port.
    """
    state = AppState(offline_mode=offline_mode)
    state.run_scan()
    handler_cls = create_api_handler_class(state)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    print(f"LEAPS Scanner server running at 0.0.0.0:{port} (Offline: {state.offline_mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
