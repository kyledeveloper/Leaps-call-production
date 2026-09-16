"""
Lightweight, zero-external-dependency HTTP API server and application state manager.
Provides endpoints for scanning, multi-board querying, instant in-memory alpha reranking,
and serves the responsive web dashboard.
Adheres strictly to Global Invariant 6 (Zero network re-fetch on alpha adjustment).
"""
import json
import os
import threading
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from src.leaps_scanner.data.webull import WebullClient
from src.leaps_scanner.data.public_delayed import PublicDelayedClient
from src.leaps_scanner.scoring.ranker import MemoryRanker, RankedItem, StrategyCandidate
from src.leaps_scanner.data.rebalancer import get_universe_manager
from src.leaps_scanner.data.universe import SymbologyNormalizer


DEFAULT_SCAN_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]
SCAN_TIERS = ("etfs", "djia", "core")
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
    def __init__(self, offline_mode: bool = False):
        _load_dotenv()
        # Live only when explicitly requested AND credentials exist.
        if not offline_mode and not (
            (os.environ.get("WEBULL_APP_KEY") or "").strip()
            and (os.environ.get("WEBULL_APP_SECRET") or "").strip()
        ):
            offline_mode = True
        self.offline_mode = offline_mode
        self.client = WebullClient(
            offline_mode=offline_mode,
            token_file=None,
        )
        if offline_mode:
            _forget_env_secrets()
        self.universe_manager = get_universe_manager(offline_mode=offline_mode)
        self.candidates: List[StrategyCandidate] = []
        self.ranker: Optional[MemoryRanker] = None
        self.last_scan_time: Optional[str] = None
        self.current_alpha: float = 0.5
        self.last_error: Optional[str] = None
        self.source: str = "sandbox" if offline_mode else "webull"
        self.connection_status: str = "sandbox" if offline_mode else "live"
        self.scan_tier: str = "etfs"
        self.scan_status: str = "idle"
        self.scan_progress: Dict[str, Any] = {"done": 0, "total": 0, "symbol": None}
        self.scanned_symbols: List[str] = []
        self._scan_cancel = False
        self._scan_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def resolve_scan_symbols(self, tier: Optional[str] = None) -> List[str]:
        chosen = (tier or self.scan_tier or "etfs").strip().lower()
        if chosen not in SCAN_TIERS:
            chosen = "etfs"
        self.scan_tier = chosen
        if chosen == "etfs":
            names = self.universe_manager.get_constituents("etfs")
        elif chosen == "djia":
            names = self.universe_manager.get_constituents("djia")
        else:
            names = sorted(self.universe_manager.get_master_universe())
        names = [SymbologyNormalizer.to_canonical(s) for s in names]
        return names or list(DEFAULT_SCAN_SYMBOLS)

    def run_scan(self, symbols: Optional[List[str]] = None, tier: Optional[str] = None) -> int:
        """Synchronous scan used by tests and sandbox mode switches."""
        if tier:
            self.scan_tier = tier
        syms = [SymbologyNormalizer.to_canonical(s) for s in symbols] if symbols else self.resolve_scan_symbols()
        self._scan_worker(syms)
        return len(self.candidates)

    def request_scan(
        self,
        symbols: Optional[List[str]] = None,
        tier: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Scan the selected universe tier.
        Sandbox runs inline. Delayed/Webull run in a background thread so the UI stays alive.
        """
        if tier:
            if tier not in SCAN_TIERS:
                return 400, {**self.public_config(), "error": "invalid_tier", "message": "tier must be etfs, djia, or core"}
            self.scan_tier = tier
        with self._lock:
            if self.scan_status == "running":
                return 409, {**self.public_config(), "error": "scan_in_progress", "message": "A scan is already running."}
            syms = [SymbologyNormalizer.to_canonical(s) for s in symbols] if symbols else self.resolve_scan_symbols()
            self.scanned_symbols = list(syms)
            self.scan_status = "running"
            self.scan_progress = {"done": 0, "total": len(syms), "symbol": None}
            self._scan_cancel = False
            async_scan = self.source in ("delayed", "webull")
        if async_scan:
            self._scan_thread = threading.Thread(target=self._scan_worker, args=(syms,), daemon=True)
            self._scan_thread.start()
            payload = self.public_config()
            payload["message"] = f"Scanning {self.scan_tier} ({len(syms)} names) in the background."
            return 202, payload
        self._scan_worker(syms)
        payload = self.public_config()
        payload["message"] = f"Scanned {self.scan_tier} ({len(self.scanned_symbols)} names)."
        return 200, payload

    def _scan_worker(self, symbols: List[str]) -> None:
        collected: List[StrategyCandidate] = []
        total = len(symbols)
        for i, sym in enumerate(symbols):
            with self._lock:
                if self._scan_cancel:
                    self.scan_status = "idle"
                    return
                self.scan_status = "running"
                self.scan_progress = {"done": i, "total": total, "symbol": sym}
            try:
                batch = self.client.get_leaps_candidates([sym])
            except Exception:
                batch = []
            collected.extend(batch)
            with self._lock:
                self.candidates = list(collected)
                self.ranker = MemoryRanker(self.candidates)
                self.last_scan_time = datetime.now(timezone.utc).isoformat()
                self.scan_progress = {"done": i + 1, "total": total, "symbol": sym}
                self.scanned_symbols = list(symbols)
        with self._lock:
            self.scan_status = "done"

    def get_boards(self, alpha: float = 0.5) -> Dict[str, List[Dict[str, Any]]]:
        self.current_alpha = alpha
        if self.ranker is None:
            self.run_scan()

        raw_boards = self.ranker.rank_boards(alpha=alpha)
        result: Dict[str, List[Dict[str, Any]]] = {}
        for b_name, items in raw_boards.items():
            result[b_name] = [asdict(it) for it in items]
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
            "current_alpha": self.current_alpha,
            "scan_tier": self.scan_tier,
            "scan_status": self.scan_status,
            "scan_progress": dict(self.scan_progress),
            "scan_symbol_count": len(self.scanned_symbols),
            "universe": {
                "etfs": len(self.universe_manager.get_constituents("etfs")),
                "djia": len(self.universe_manager.get_constituents("djia")),
                "core": len(self.universe_manager.get_master_universe()),
            },
            "strategies": ["deep_itm", "vol_discount", "oversold", "unusual_flow"],
        }

    def set_mode(
        self,
        offline: bool = True,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        source: Optional[str] = None,
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

        self._scan_cancel = True
        thread = self._scan_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self.scan_status = "idle"
            self._scan_cancel = False

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
                self.client = PublicDelayedClient()
                self.universe_manager = get_universe_manager(offline_mode=True)
                self.offline_mode = False
                self.source = "delayed"
                self.connection_status = "delayed"
                self.last_error = None
                scan_after = "async"

            else:
                key = (app_key or (getattr(self.client, "app_key", None) if self.client else None) or "").strip()
                secret = (app_secret or (getattr(self.client, "app_secret", None) if self.client else None) or "").strip()
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
            count = self.run_scan()
            payload = self.public_config()
            payload["message"] = "Switched to sandbox mock data."
            payload["candidate_count"] = count
            return 200, payload

        code, payload = self.request_scan()
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

            # GET / or /index.html
            if method in ("GET", "HEAD") and clean_path in ("/", "/index.html"):
                index_path = os.path.join(static_dir, "index.html")
                if os.path.exists(index_path):
                    with open(index_path, "rb") as f:
                        content = f.read()
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, content if method == "GET" else b""
                else:
                    html = b"<h1>LEAPS Scanner API</h1>"
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, html if method == "GET" else b""

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
                )
                return code, headers, json.dumps(payload).encode("utf-8")

            # POST /api/v1/scan
            if method == "POST" and clean_path == "/api/v1/scan":
                symbols = None
                tier = None
                if body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                        symbols = req_data.get("symbols")
                        tier = req_data.get("tier")
                    except Exception:
                        pass
                if isinstance(tier, str):
                    tier = tier.strip().lower() or None
                else:
                    tier = None
                if symbols is not None and not isinstance(symbols, list):
                    symbols = None
                code, payload = state.request_scan(symbols=symbols, tier=tier)
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

            # GET /api/v1/universe
            if method == "GET" and clean_path in ("/api/v1/universe", "/api/universe"):
                data = {
                    "status": "healthy",
                    "indices": {
                        "sp100": len(state.universe_manager.get_constituents("sp100")),
                        "nasdaq100": len(state.universe_manager.get_constituents("nasdaq100")),
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
