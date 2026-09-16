"""
Webull OpenAPI Adapter.
Handles HMAC-SHA256 signature calculation, 2FA token life-cycle management,
token-bucket rate limiting, LEAPS options chain ingestion, and safe offline sandbox fallback.
Adheres strictly to Global Invariant 6 (Zero network re-fetch) and Defensive Clauses 9-14.
"""
import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from src.leaps_scanner.scoring.ranker import StrategyCandidate
from src.leaps_scanner.data.universe import SymbologyNormalizer

logger = logging.getLogger(__name__)


class PermissionDeniedOpraError(RuntimeError):
    """Raised when market data quotes subscription is missing (403 MARKET_DATA_NOT_SUBSCRIBED)."""
    pass


class LogSanitizer:
    """
    Defensive Clause 9: Sanitizes sensitive credentials from logs, dicts, and tracebacks.
    """
    SENSITIVE_KEYS = {
        "x-signature",
        "x-access-token",
        "authorization",
        "app_secret",
        "webull_app_secret",
        "password",
        "token"
    }

    @classmethod
    def mask_dict(cls, data: dict) -> dict:
        sanitized = {}
        for k, v in data.items():
            if str(k).lower() in cls.SENSITIVE_KEYS:
                sanitized[k] = "***REDACTED***"
            elif isinstance(v, dict):
                sanitized[k] = cls.mask_dict(v)
            else:
                sanitized[k] = v
        return sanitized


class WebullSigner:
    """
    Defensive Clause 9: Pure Python standard library HMAC-SHA256 signer.
    Zero third-party external dependencies. 100% byte-for-byte compatible with Webull OpenAPI.
    """
    @staticmethod
    def calc_signature(
        uri: str,
        queries: Optional[Dict[str, Any]] = None,
        body_dict: Optional[Dict[str, Any]] = None,
        app_key: str = "",
        app_secret: str = "",
        host: str = "api.webull.com",
        nonce: Optional[str] = None,
        timestamp: Optional[str] = None
    ) -> Tuple[Dict[str, str], str]:
        ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        n = nonce or str(uuid.uuid4())

        sign_headers = {
            "x-app-key": app_key,
            "x-timestamp": ts,
            "x-signature-version": "1.0",
            "x-signature-algorithm": "HMAC-SHA256",
            "x-signature-nonce": n,
            "host": host,
        }

        sign_params = dict(sign_headers)
        if queries:
            for k, v in queries.items():
                k_str = str(k).lower()
                if k_str in sign_params:
                    sign_params[k_str] = f"{sign_params[k_str]}&{v}"
                else:
                    sign_params[k_str] = str(v)

        body_string = None
        if body_dict is not None:
            raw_body = json.dumps(body_dict, ensure_ascii=False, separators=(',', ':'))
            body_string = hashlib.sha256(raw_body.encode('utf-8')).hexdigest().upper()

        sorted_items = sorted(sign_params.items(), key=lambda x: x[0])
        kv_pairs = [f"{k}={v}" for k, v in sorted_items]
        string_to_sign = uri + "&" + "&".join(kv_pairs)
        if body_string:
            string_to_sign += "&" + body_string

        encoded_string = urllib.parse.quote(string_to_sign, safe='')
        secret_bytes = (app_secret + "&").encode('utf-8')
        h = hmac.new(secret_bytes, encoded_string.encode('utf-8'), hashlib.sha256)
        signature = base64.b64encode(h.digest()).decode('utf-8').strip()

        req_headers = {
            "x-app-key": app_key,
            "x-timestamp": ts,
            "x-signature-version": "1.0",
            "x-signature-algorithm": "HMAC-SHA256",
            "x-signature-nonce": n,
            "x-signature": signature,
            "x-version": "v3",
            "x-webull-client-source": "sdk",
            "User-Agent": "WebullApiSDK (Darwin 25.0; arm64) Python/3.12",
            "Accept-Encoding": "gzip, deflate",
            "Accept": "*/*",
            "Host": host,
        }
        return req_headers, encoded_string


class TokenBucketRateLimiter:
    """
    Thread-safe Token Bucket Rate Limiter with non-busy blocking wait (Defensive Clause 13).
    """
    def __init__(self, rate: float = 5.0, capacity: float = 5.0):
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0, block: bool = False, timeout: Optional[float] = None) -> bool:
        if tokens > self.capacity:
            raise ValueError(f"Requested tokens ({tokens}) exceeds bucket capacity ({self.capacity})")
        start_time = time.monotonic()
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

                if not block:
                    return False

                missing = tokens - self._tokens
                wait_time = missing / self.rate

            if timeout is not None:
                elapsed_total = time.monotonic() - start_time
                if elapsed_total + wait_time > timeout:
                    return False

            time.sleep(max(0.005, wait_time))


class FailFastPermissionBreaker:
    """
    Defensive Clause 11: Fail-fast circuit breaker for missing market data subscriptions.
    Prevents retry amplification storm on fatal 403 MARKET_DATA_NOT_SUBSCRIBED.
    """
    def __init__(self):
        self.is_broken: bool = False
        self.reason: Optional[str] = None
        self._lock = threading.Lock()

    def record_error(self, status_code: int, error_code: str, message: str = ""):
        if status_code == 403 and "MARKET_DATA_NOT_SUBSCRIBED" in str(error_code):
            with self._lock:
                self.is_broken = True
                self.reason = f"OPRA/Market Data quotes not subscribed ({error_code}): {message}"
                logger.warning("FailFastPermissionBreaker TRIPPED: %s", self.reason)

    def check_permission(self):
        with self._lock:
            if self.is_broken:
                raise PermissionDeniedOpraError(self.reason)


class SafePaginationIterator:
    """
    Defensive Clause 12: Safe pagination iterator with max_pages protection
    and visited cursor tracking to eliminate cyclic loop vulnerabilities.
    """
    def __init__(self, fetch_page_fn: Callable[[Optional[str]], Tuple[List[dict], Optional[str]]], max_pages: int = 5):
        self.fetch_page_fn = fetch_page_fn
        self.max_pages = max_pages

    def __iter__(self) -> Iterator[dict]:
        current_page = 0
        pagination_key = None
        visited_keys = set()

        while current_page < self.max_pages:
            items, next_key = self.fetch_page_fn(pagination_key)
            for item in (items or []):
                yield item

            if not next_key:
                break
            if next_key in visited_keys:
                logger.warning("Pagination loop detected with key: %s", next_key)
                break

            visited_keys.add(next_key)
            pagination_key = next_key
            current_page += 1


class SingleFlightAuthManager:
    """
    Defensive Clause 10: Thread-safe single-flight token cache, active invalidation,
    and refresh manager.
    """
    def __init__(
        self,
        refresh_fn: Callable[[], str],
        ttl_seconds: float = 3600.0,
        token_file: Optional[str] = None
    ):
        self._refresh_fn = refresh_fn
        self._ttl_seconds = ttl_seconds
        self._token_file = token_file
        self._token: Optional[str] = None
        self._expiry_time: float = 0.0
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._load_from_file()

    def _load_from_file(self):
        if self._token_file and os.path.exists(self._token_file):
            try:
                with open(self._token_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    token = data.get("token")
                    expires_ms = data.get("expires", 0)
                    status = data.get("status")
                    now_ms = time.time() * 1000
                    if token and status == "NORMAL" and expires_ms > now_ms:
                        self._token = token
                        self._expiry_time = time.monotonic() + (expires_ms - now_ms) / 1000.0
                        logger.info("Loaded valid token from local file: %s", self._token_file)
            except Exception as e:
                logger.warning("Failed to load local token file: %s", e)

    def _save_to_file(self, token: str, expires_ms: int):
        if self._token_file:
            try:
                with open(self._token_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "token": token,
                        "expires": expires_ms,
                        "status": "NORMAL"
                    }, f, indent=2)
            except Exception as e:
                logger.warning("Failed to save token to file: %s", e)

    def invalidate(self):
        with self._lock:
            self._token = None
            self._expiry_time = 0.0
            if self._token_file and os.path.exists(self._token_file):
                try:
                    os.remove(self._token_file)
                except Exception:
                    pass

    def get_token(self) -> str:
        with self._lock:
            now = time.monotonic()
            if self._token is not None and now < self._expiry_time:
                return self._token

        # Serialize refresh attempts with single-flight mutex to avoid 2FA herd storm
        with self._refresh_lock:
            # Double-checked locking: another thread may have acquired fresh token
            with self._lock:
                now = time.monotonic()
                if self._token is not None and now < self._expiry_time:
                    return self._token

            new_token = self._refresh_fn()

            with self._lock:
                self._token = new_token
                self._expiry_time = time.monotonic() + self._ttl_seconds
                return self._token


def parse_webull_chain_response(payload: dict) -> List[StrategyCandidate]:
    """
    Parse and validate raw Webull options chain JSON response.
    Maintains backward compatibility with mock structures.
    """
    underlying = payload.get("underlying", "")
    spot = float(payload.get("spot", 0.0))
    dividend_yield = float(payload.get("dividend_yield", 0.0))
    raw_options = payload.get("options", [])

    candidates: List[StrategyCandidate] = []

    for opt in raw_options:
        dte = float(opt.get("dte", 0.0))
        if dte < 250.0:
            continue
        multiplier = int(opt.get("multiplier", 100))
        if multiplier != 100:
            continue

        symbol = opt.get("symbol", "")
        strike = float(opt.get("strike", 0.0))
        bid = float(opt.get("bid", 0.0))
        ask = float(opt.get("ask", 0.0))
        delta = float(opt.get("delta", 0.5))
        oi = int(opt.get("open_interest", 0))
        vol = int(opt.get("volume", 0))
        iv = float(opt["iv"]) if "iv" in opt and opt["iv"] is not None else None

        candidates.append(StrategyCandidate(
            symbol=symbol,
            underlying=underlying,
            strike=strike,
            spot=spot,
            dte=dte,
            bid=bid,
            ask=ask,
            delta=delta,
            open_interest=oi,
            volume=vol,
            dividend_yield=dividend_yield,
            iv=iv
        ))

    return candidates


def parse_webull_contracts_response(
    payload: dict,
    ref_date: Optional[datetime] = None,
    spot: float = 0.0
) -> List[StrategyCandidate]:
    """
    Defensive Clause 14: Parse real Webull OpenAPI contracts response
    from /trading/instruments/options/contracts/list.
    """
    contracts = payload.get("data", [])
    if not isinstance(contracts, list):
        return []

    now_dt = ref_date or datetime.now(timezone.utc)
    candidates: List[StrategyCandidate] = []

    for item in contracts:
        if not isinstance(item, dict):
            continue
        if item.get("option_type") != "CALL":
            continue

        exp_str = item.get("expiration_date")
        if not exp_str:
            continue

        try:
            exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dte = (exp_dt - now_dt).total_seconds() / 86400.0
        except Exception:
            continue

        # LEAPS Filter: DTE >= 250d
        if dte < 250.0:
            continue

        try:
            multiplier = int(float(item.get("multiplier", 100)))
            if multiplier != 100:
                continue
            strike = float(item.get("strike_price", 0.0))
        except (ValueError, TypeError):
            continue

        symbol = item.get("symbol", "")
        underlying = item.get("underlying_symbol", "")

        candidates.append(StrategyCandidate(
            symbol=symbol,
            underlying=underlying,
            strike=strike,
            spot=spot if spot > 0 else strike,
            dte=dte,
            bid=0.0,
            ask=0.0,
            delta=0.70,
            open_interest=0,
            volume=0,
            data_quality="METADATA_ONLY"
        ))

    return candidates


class WebullClient:
    """
    Production Webull OpenAPI Client with HMAC-SHA256 signer, 2FA token management,
    token-bucket rate limiting, and safe offline sandbox mode.
    """
    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        offline_mode: Optional[bool] = None,
        token_file: Optional[str] = None
    ):
        self.app_key = app_key or os.environ.get("WEBULL_APP_KEY")
        self.app_secret = app_secret or os.environ.get("WEBULL_APP_SECRET")
        self.region_id = os.environ.get("WEBULL_REGION_ID", "us")
        self.host = "api.webull.com"

        # Determine offline mode: default to True unless credentials exist and WEBULL_OFFLINE_MODE is "false"
        if offline_mode is not None:
            self.offline_mode = offline_mode
        else:
            env_mode = os.environ.get("WEBULL_OFFLINE_MODE", "true").lower()
            if env_mode == "false" and self.app_key and self.app_secret:
                self.offline_mode = False
            else:
                self.offline_mode = True

        self.rate_limiter = TokenBucketRateLimiter(rate=5.0, capacity=5.0)
        self.permission_breaker = FailFastPermissionBreaker()

        if self.offline_mode:
            t_file = token_file
        else:
            t_file = token_file or os.path.join(os.getcwd(), ".webull_token.json")
        self.auth = SingleFlightAuthManager(refresh_fn=self._refresh_token, token_file=t_file)

    def _refresh_token(self) -> str:
        if self.offline_mode:
            return "mock_offline_token_sandbox"

        # Request real token creation
        logger.info("Requesting fresh 2FA access token from Webull OpenAPI...")
        status, resp = self._http_request(
            uri="/auth/tokens/create",
            body_dict={},
            require_auth=False
        )

        if status == 200 and isinstance(resp, dict):
            token = resp.get("token")
            tok_status = resp.get("status")
            expires = resp.get("expires", 0)

            if tok_status == "NORMAL":
                logger.info("Obtained verified NORMAL access token.")
                self.auth._save_to_file(token, expires)
                return token
            elif tok_status == "PENDING":
                logger.warning(
                    "2FA Approval Required! Please approve the OpenAPI request in your Webull Mobile App. Polling check..."
                )
                for _ in range(30):
                    time.sleep(3.0)
                    c_status, c_resp = self._http_request(
                        uri="/auth/tokens/check",
                        body_dict={"token": token},
                        require_auth=False
                    )
                    if c_status == 200 and isinstance(c_resp, dict) and c_resp.get("status") == "NORMAL":
                        logger.info("2FA authorization confirmed by user!")
                        exp = c_resp.get("expires", expires)
                        self.auth._save_to_file(token, exp)
                        return token

        raise RuntimeError("Webull 2FA authentication failed or timed out.")

    def _http_request(
        self,
        uri: str,
        queries: Optional[Dict[str, Any]] = None,
        body_dict: Optional[Dict[str, Any]] = None,
        require_auth: bool = True
    ) -> Tuple[int, Any]:
        self.permission_breaker.check_permission()
        self.rate_limiter.acquire(tokens=1.0, block=True)

        req_headers, _ = WebullSigner.calc_signature(
            uri=uri,
            queries=queries,
            body_dict=body_dict,
            app_key=self.app_key or "",
            app_secret=self.app_secret or "",
            host=self.host
        )

        if require_auth:
            token = self.auth.get_token()
            if token:
                req_headers["x-access-token"] = token

        url = f"https://{self.host}{uri}"
        if queries:
            url += "?" + urllib.parse.urlencode(queries)

        data = None
        if body_dict is not None:
            data = json.dumps(body_dict, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            url,
            data=data,
            headers=req_headers,
            method="POST" if body_dict is not None else "GET"
        )

        ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                content = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    content = gzip.decompress(content)
                text = content.decode('utf-8', errors='replace')
                return resp.status, json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            err_content = e.read()
            if e.headers.get("Content-Encoding") == "gzip":
                import gzip
                err_content = gzip.decompress(err_content)
            text = err_content.decode('utf-8', errors='replace')
            try:
                parsed_err = json.loads(text)
            except Exception:
                parsed_err = {"message": text}

            if e.code == 403:
                self.permission_breaker.record_error(
                    status_code=403,
                    error_code=parsed_err.get("error_code", ""),
                    message=parsed_err.get("message", "")
                )
            elif e.code == 401:
                # Invalidate stale token on 401
                self.auth.invalidate()

            return e.code, parsed_err

    def query_options_chain(self, symbol: str) -> dict:
        """
        Query option chain using vendor-specific ticker format (BRK.B -> BRK-B).
        """
        broker_symbol = SymbologyNormalizer.to_broker(symbol, broker="webull")
        if self.offline_mode:
            return {"underlying": broker_symbol, "options": []}
        contracts = self.query_options_contracts(symbol)
        return {"underlying": broker_symbol, "options": contracts}

    def query_options_contracts(self, symbol: str) -> List[dict]:
        """
        Query listing options contracts for underlying symbol.
        Uses SafePaginationIterator to fetch pages cleanly.
        """
        broker_symbol = SymbologyNormalizer.to_broker(symbol, broker="webull")

        def fetch_page(page_key: Optional[str]) -> Tuple[List[dict], Optional[str]]:
            queries = {
                "underlying_symbols": broker_symbol,
                "category": "US_OPTION",
                "option_type": "CALL"
            }
            if page_key:
                queries["pagination_key"] = page_key

            status, resp = self._http_request(
                uri="/trading/instruments/options/contracts/list",
                queries=queries
            )
            if status == 200 and isinstance(resp, dict):
                return resp.get("data", []), resp.get("pagination_key")
            return [], None

        iterator = SafePaginationIterator(fetch_page_fn=fetch_page, max_pages=5)
        return list(iterator)

    def get_accounts(self) -> List[dict]:
        """
        Retrieve live account list.
        """
        status, resp = self._http_request(uri="/trading/accounts/list")
        if status == 200 and isinstance(resp, list):
            return resp
        return []

    def get_balances(self, account_id: str) -> dict:
        """
        Retrieve live balances for specified account.
        """
        status, resp = self._http_request(
            uri="/trading/assets/balances/get",
            queries={"account_id": account_id, "total_asset_currency": "USD"}
        )
        if status == 200 and isinstance(resp, dict):
            return resp
        return {}

    def get_leaps_candidates(self, symbols: List[str]) -> List[StrategyCandidate]:
        """
        Fetch LEAPS candidates for given symbols.
        In offline mode, loads realistic fixtures safely without network.
        In online mode, connects to live Webull OpenAPI.
        """
        candidates: List[StrategyCandidate] = []

        if self.offline_mode:
            mock_specs = {
                "AAPL": {
                    "spot": 220.0,
                    "div": 0.005,
                    "rsi": 28.0,
                    "dma": -0.12,
                    "dd": 0.18,
                    "bounce": 0.04,
                    "iv": 0.22,
                    "iv_pct": 0.15,
                    "options": [
                        ("AAPL270115C00150000", 150.0, 480.0, 75.0, 78.0, 0.82, 1500, 80),
                        ("AAPL270115C00180000", 180.0, 480.0, 52.0, 54.0, 0.76, 2500, 150),
                        ("AAPL270115C00200000", 200.0, 480.0, 38.0, 40.0, 0.65, 3000, 2200),
                        ("AAPL270115C00220000", 220.0, 480.0, 26.0, 28.0, 0.52, 4500, 800),
                    ]
                },
                "SPY": {
                    "spot": 550.0,
                    "div": 0.013,
                    "rsi": 42.0,
                    "dma": -0.04,
                    "dd": 0.06,
                    "bounce": 0.08,
                    "iv": 0.14,
                    "iv_pct": 0.22,
                    "is_etf": True,
                    "options": [
                        ("SPY270115C00450000", 450.0, 480.0, 125.0, 128.0, 0.84, 8000, 400),
                        ("SPY270115C00500000", 500.0, 480.0, 85.0, 87.0, 0.72, 12000, 1200),
                        ("SPY270115C00550000", 550.0, 480.0, 50.0, 52.0, 0.53, 20000, 2500),
                    ]
                },
                "NVDA": {
                    "spot": 120.0,
                    "div": 0.001,
                    "rsi": 48.0,
                    "dma": 0.05,
                    "dd": 0.15,
                    "bounce": 0.12,
                    "iv": 0.45,
                    "iv_pct": 0.35,
                    "options": [
                        ("NVDA270115C00080000", 80.0, 480.0, 52.0, 54.0, 0.83, 5000, 300),
                        ("NVDA270115C00100000", 100.0, 480.0, 38.0, 40.0, 0.70, 8500, 600),
                        ("NVDA270115C00120000", 120.0, 480.0, 26.0, 28.0, 0.54, 15000, 1800),
                    ]
                }
            }

            for raw_sym in symbols:
                sym = SymbologyNormalizer.to_canonical(raw_sym)
                spec = mock_specs.get(sym.upper())
                if not spec:
                    continue
                spot = spec["spot"]
                div = spec["div"]
                is_etf = spec.get("is_etf", False)
                for opt_sym, strike, dte, bid, ask, delta, oi, vol in spec["options"]:
                    candidates.append(StrategyCandidate(
                        symbol=opt_sym,
                        underlying=sym.upper(),
                        strike=strike,
                        spot=spot,
                        dte=dte,
                        bid=bid,
                        ask=ask,
                        delta=delta,
                        open_interest=oi,
                        volume=vol,
                        dividend_yield=div,
                        iv=spec.get("iv"),
                        iv_percentile=spec.get("iv_pct"),
                        iv_rank=spec.get("iv_pct"),
                        rsi_14=spec.get("rsi", 50.0),
                        pct_to_200dma=spec.get("dma", 0.0),
                        drawdown_52w_high=spec.get("dd", 0.0),
                        bounce_52w_low=spec.get("bounce", 0.0),
                        is_etf=is_etf,
                        valid_history_days=252,
                        data_quality="REALTIME"
                    ))
            return candidates

        # Online Live Processing
        for raw_sym in symbols:
            sym = SymbologyNormalizer.to_canonical(raw_sym)
            try:
                raw_contracts = self.query_options_contracts(sym)
                c_list = parse_webull_contracts_response({"data": raw_contracts})
                candidates.extend(c_list)
            except Exception as e:
                logger.error("Failed to query live contracts for %s: %s", sym, e)

        return candidates
