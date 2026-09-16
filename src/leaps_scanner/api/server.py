"""
Lightweight, zero-external-dependency HTTP API server and application state manager.
Provides endpoints for scanning, multi-board querying, instant in-memory alpha reranking,
and serves the responsive web dashboard.
Adheres strictly to Global Invariant 6 (Zero network re-fetch on alpha adjustment).
"""
import json
import os
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from src.leaps_scanner.data.webull import WebullClient
from src.leaps_scanner.scoring.ranker import MemoryRanker, RankedItem, StrategyCandidate
from src.leaps_scanner.data.rebalancer import get_universe_manager
from src.leaps_scanner.data.universe import SymbologyNormalizer


DEFAULT_SCAN_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]


class AppState:
    """
    Central application state holding current cache of LEAPS candidates, ranker, and universe manager.
    """
    def __init__(self, offline_mode: bool = False):
        self.offline_mode = offline_mode
        self.client = WebullClient(offline_mode=offline_mode)
        self.universe_manager = get_universe_manager(offline_mode=offline_mode)
        self.candidates: List[StrategyCandidate] = []
        self.ranker: Optional[MemoryRanker] = None
        self.last_scan_time: Optional[str] = None
        self.current_alpha: float = 0.5

    def run_scan(self, symbols: Optional[List[str]] = None) -> int:
        syms = symbols if symbols else DEFAULT_SCAN_SYMBOLS
        syms = [SymbologyNormalizer.to_canonical(s) for s in syms]
        self.candidates = self.client.get_leaps_candidates(syms)
        self.ranker = MemoryRanker(self.candidates)
        self.last_scan_time = datetime.now(timezone.utc).isoformat()
        return len(self.candidates)

    def get_boards(self, alpha: float = 0.5) -> Dict[str, List[Dict[str, Any]]]:
        self.current_alpha = alpha
        if self.ranker is None:
            self.run_scan()

        raw_boards = self.ranker.rank_boards(alpha=alpha)
        result: Dict[str, List[Dict[str, Any]]] = {}
        for b_name, items in raw_boards.items():
            result[b_name] = [asdict(it) for it in items]
        return result


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
            if method == "GET" and clean_path in ("/", "/index.html"):
                index_path = os.path.join(static_dir, "index.html")
                if os.path.exists(index_path):
                    with open(index_path, "rb") as f:
                        content = f.read()
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, content
                else:
                    return 200, {"Content-Type": "text/html; charset=utf-8"}, b"<h1>LEAPS Scanner API</h1>"

            # GET /api/v1/config
            if method == "GET" and clean_path == "/api/v1/config":
                data = {
                    "status": "healthy",
                    "offline_mode": state.offline_mode,
                    "last_scan_time": state.last_scan_time,
                    "candidate_count": len(state.candidates),
                    "current_alpha": state.current_alpha,
                    "strategies": ["deep_itm", "vol_discount", "oversold", "unusual_flow"]
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # POST /api/v1/scan
            if method == "POST" and clean_path == "/api/v1/scan":
                symbols = None
                if body:
                    try:
                        req_data = json.loads(body.decode("utf-8"))
                        symbols = req_data.get("symbols")
                    except Exception:
                        pass
                count = state.run_scan(symbols)
                data = {
                    "message": "Scan completed successfully",
                    "candidate_count": count,
                    "timestamp": state.last_scan_time
                }
                return 200, headers, json.dumps(data).encode("utf-8")

            # GET /api/v1/boards
            if method == "GET" and clean_path == "/api/v1/boards":
                alpha = float(query_params.get("alpha", [state.current_alpha])[0])
                boards = state.get_boards(alpha=alpha)
                data = {
                    "alpha": alpha,
                    "boards": boards,
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
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    print(f"🚀 LEAPS Scanner server running at http://127.0.0.1:{port} (Offline: {offline_mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
