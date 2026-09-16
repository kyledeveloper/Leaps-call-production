"""
Automated Dynamic Index Rebalancing Engine and Constituent Manager.
Handles periodic or on-demand rebalance checks, multi-source ingestion,
canonical symbology normalization, process file locking, and corrupted cache self-healing.
"""
import os
import re
import json
import hashlib
import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Dict, List, Set, Any, Optional
import urllib.request
import urllib.error
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

from src.leaps_scanner.data.universe import (
    SP100_COMPONENTS,
    NASDAQ100_COMPONENTS,
    DJIA_COMPONENTS,
    CORE_ETFS,
    SELECTED_ADRS,
    SymbologyNormalizer
)

logger = logging.getLogger(__name__)

# RFC-compliant User Agent for Wikimedia & public financial endpoints
USER_AGENT = "LeapsScanner/1.0 (quant-contact@internal.lan; dev@leaps-call.local)"

# Strict regex white-list for equity symbols: 1-5 letters optionally followed by dot + 1-2 letters
TICKER_REGEX = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$")


class RebalanceValidationError(Exception):
    """Raised when scraped or ingested constituents violate integrity or cardinality bounds."""
    pass


class _TableHTMLParser(HTMLParser):
    """Minimal zero-dependency HTML table parser targeting index constituent tables."""
    def __init__(self):
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._current_table: Optional[List[List[str]]] = None
        self._current_row: Optional[List[str]] = None
        self._current_cell: Optional[str] = None
        self._in_table = False
        self._in_row = False
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_row and tag in ("th", "td"):
            self._in_cell = True
            self._current_cell = ""

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("th", "td") and self._in_cell:
            self._in_cell = False
            if self._current_row is not None and self._current_cell is not None:
                self._current_row.append(self._current_cell.strip())
            self._current_cell = None
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_table is not None and self._current_row is not None and len(self._current_row) > 0:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._in_table:
            self._in_table = False
            if self._current_table is not None:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data):
        if self._in_cell and self._current_cell is not None:
            self._current_cell += data


class WikipediaConstituentFetcher:
    """
    Fetches and parses authoritative constituent tables from Wikipedia.
    Enforces header signatures, symbol regex sanitization, and cardinality gates.
    """
    URL_MAP = {
        "djia": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
        "sp100": "https://en.wikipedia.org/wiki/S%26P_100",
        "nasdaq100": "https://en.wikipedia.org/wiki/Nasdaq-100"
    }

    def __init__(self, offline_mode: bool = False, timeout: float = 3.0):
        self.offline_mode = offline_mode
        self.timeout = timeout

    def _sanitize_symbol(self, raw_symbol: str) -> Optional[str]:
        """Strip footnotes, exchange prefixes, and non-alphanumeric noise."""
        s = raw_symbol.strip()
        # Remove footnote citations: [1], [note 1], etc.
        s = re.sub(r"\[.*?\]", "", s)
        # Remove exchange prefixes like NYSE: or NASDAQ:
        s = re.sub(r"^(NYSE|NASDAQ|AMEX):\s*", "", s, flags=re.IGNORECASE)
        # Remove special characters like asterisks or HTML artifacts
        s = re.sub(r"[\*\xa0]", "", s).strip()
        
        canonical = SymbologyNormalizer.to_canonical(s)
        if TICKER_REGEX.match(canonical):
            return canonical
        return None

    def parse_html_table(self, html_content: str, index_name: str) -> List[str]:
        """
        Parse raw HTML to locate the constituent table matching required header signatures.
        Enforces strict cardinality gates per index.
        """
        parser = _TableHTMLParser()
        parser.feed(html_content)

        idx = index_name.lower().strip()
        extracted_symbols: List[str] = []

        for table in parser.tables:
            if not table:
                continue
            headers = [h.lower() for h in table[0]]
            
            # Look for symbol column index
            symbol_col_idx = -1
            for col_i, header in enumerate(headers):
                if any(kw in header for kw in ["symbol", "ticker"]):
                    symbol_col_idx = col_i
                    break

            if symbol_col_idx == -1:
                continue

            # Found candidates table, parse subsequent rows
            current_candidates: List[str] = []
            for row in table[1:]:
                if len(row) > symbol_col_idx:
                    sanitized = self._sanitize_symbol(row[symbol_col_idx])
                    if sanitized:
                        current_candidates.append(sanitized)

            # Check if this table has viable constituent count
            if idx == "djia" and len(current_candidates) >= 25:
                extracted_symbols = current_candidates
                break
            elif idx in ("sp100", "nasdaq100") and len(current_candidates) >= 80:
                extracted_symbols = current_candidates
                break

        # Cardinality Gatekeeper checks
        count = len(extracted_symbols)
        if idx == "djia":
            if count != 30:
                raise RebalanceValidationError(f"DJIA cardinality violation: expected exactly 30, got {count}")
        elif idx == "sp100":
            if not (95 <= count <= 110):
                raise RebalanceValidationError(f"S&P 100 cardinality violation: expected [95, 110], got {count}")
        elif idx == "nasdaq100":
            if not (95 <= count <= 110):
                raise RebalanceValidationError(f"Nasdaq 100 cardinality violation: expected [95, 110], got {count}")
        else:
            if count == 0:
                raise RebalanceValidationError(f"No constituents found for index: {index_name}")

        return sorted(list(set(extracted_symbols)))

    def fetch_online(self, index_name: str) -> List[str]:
        """Fetch remote URL and return parsed constituents."""
        if self.offline_mode:
            raise RebalanceValidationError("Offline mode enabled; cannot fetch remote URL")

        url = self.URL_MAP.get(index_name.lower().strip())
        if not url:
            raise RebalanceValidationError(f"No known URL for index: {index_name}")

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                html_bytes = response.read()
                html_content = html_bytes.decode("utf-8", errors="replace")
                return self.parse_html_table(html_content, index_name=index_name)
        except Exception as e:
            raise RebalanceValidationError(f"Failed to fetch {index_name} from {url}: {e}") from e


class DynamicUniverseManager:
    """
    Manages persistent, versioned universe constituents and rebalancing event logs.
    Includes process file locking, sha256 verification, and automatic corruption self-healing.
    """
    DEFAULT_CACHE_REL_PATH = os.path.join(
        os.path.dirname(__file__), "store", "universe_cache.json"
    )

    def __init__(
        self,
        cache_path: Optional[str] = None,
        offline_mode: bool = True,
        pinned_symbols: Optional[Set[str]] = None
    ):
        self.cache_path = cache_path or self.DEFAULT_CACHE_REL_PATH
        self.offline_mode = offline_mode
        self.pinned_symbols: Set[str] = set(
            SymbologyNormalizer.to_canonical(s) for s in (pinned_symbols or set())
        )
        self._indices: Dict[str, List[str]] = {}
        self._rebalance_history: List[Dict[str, Any]] = []
        self._last_synced: Optional[str] = None
        self._load_or_initialize()

    def pin_symbol(self, symbol: str) -> None:
        """Pin active contract/holding into monitored universe under REMOVED_GRACE_PERIOD."""
        self.pinned_symbols.add(SymbologyNormalizer.to_canonical(symbol))

    def _get_static_seeds(self) -> Dict[str, List[str]]:
        return {
            "sp100": sorted(list(set(SP100_COMPONENTS))),
            "nasdaq100": sorted(list(set(NASDAQ100_COMPONENTS))),
            "djia": sorted(list(set(DJIA_COMPONENTS))),
            "etfs": sorted(list(set(CORE_ETFS))),
            "adrs": sorted(list(set(SELECTED_ADRS)))
        }

    def _load_or_initialize(self) -> None:
        if not os.path.exists(self.cache_path):
            self._indices = self._get_static_seeds()
            self._rebalance_history = []
            self._last_synced = datetime.now(timezone.utc).isoformat()
            self._save_cache()
            return

        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                raw_data = f.read()

            data = json.loads(raw_data)
            expected_sha = data.get("sha256")
            payload = data.get("payload", {})
            indices = payload.get("indices", {})

            # Validate structure
            if not indices or "djia" not in indices:
                raise ValueError("Cache missing required index entries")

            # Check sha256 integrity
            payload_str = json.dumps(payload, sort_keys=True)
            actual_sha = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            if expected_sha and actual_sha != expected_sha:
                raise ValueError(f"Checksum mismatch: expected {expected_sha}, got {actual_sha}")

            self._indices = indices
            self._rebalance_history = payload.get("rebalance_history", [])
            self._last_synced = payload.get("last_synced")

        except Exception as e:
            logger.warning(f"Universe cache corrupted or invalid at {self.cache_path}: {e}. Quarantining and fallback to seed.")
            # Quarantine corrupted cache
            try:
                quarantine_path = self.cache_path + ".corrupt.bak"
                if os.path.exists(self.cache_path):
                    os.replace(self.cache_path, quarantine_path)
            except Exception as quarantine_err:
                logger.error(f"Failed to quarantine corrupted cache: {quarantine_err}")

            self._indices = self._get_static_seeds()
            self._rebalance_history = []
            self._last_synced = datetime.now(timezone.utc).isoformat()
            self._save_cache()

    def _save_cache(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
        payload = {
            "indices": self._indices,
            "rebalance_history": self._rebalance_history,
            "last_synced": self._last_synced
        }
        payload_str = json.dumps(payload, sort_keys=True)
        sha = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        wrapped = {
            "version": 1,
            "sha256": sha,
            "payload": payload
        }

        # Thread & process file lock to prevent concurrent write collisions
        lock_file = self.cache_path + ".lock"
        with open(lock_file, "w") as lf:
            if HAS_FCNTL:
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                except Exception:
                    pass
            try:
                tmp_path = self.cache_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(wrapped, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.cache_path)
            finally:
                if HAS_FCNTL:
                    try:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass

    def get_constituents(self, index_name: str) -> List[str]:
        idx = index_name.lower().strip()
        return sorted(list(set(self._indices.get(idx, []))))

    def get_master_universe(self) -> Set[str]:
        """Compute strict mathematical union across all active indices and pinned holdings."""
        master: Set[str] = set()
        for components in self._indices.values():
            master.update(components)
        master.update(self.pinned_symbols)
        return master

    def get_rebalance_history(self) -> List[Dict[str, Any]]:
        return list(self._rebalance_history)

    def apply_rebalance(
        self,
        index_name: str,
        new_constituents: List[str],
        reason: str = "Scheduled or committee rebalance"
    ) -> Dict[str, List[str]]:
        """
        Applies new constituents to specified index, records diff events,
        and saves updated cache atomically.
        """
        idx = index_name.lower().strip()
        old_set = set(self._indices.get(idx, []))
        new_set = set(new_constituents)

        added = sorted(list(new_set - old_set))
        removed = sorted(list(old_set - new_set))

        if added or removed:
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "index": idx,
                "added": added,
                "removed": removed,
                "reason": reason
            }
            self._rebalance_history.append(event)
            self._indices[idx] = sorted(list(new_set))
            self._last_synced = datetime.now(timezone.utc).isoformat()
            self._save_cache()

        return {"added": added, "removed": removed}

    def sync_index(
        self,
        index_name: str,
        fetcher: Optional[WikipediaConstituentFetcher] = None
    ) -> Dict[str, Any]:
        """
        Synchronizes an index by fetching fresh constituents from the remote source.
        Returns rebalance diff on success.
        """
        if fetcher is None:
            fetcher = WikipediaConstituentFetcher(offline_mode=self.offline_mode)

        try:
            fresh = fetcher.fetch_online(index_name)
            diff = self.apply_rebalance(
                index_name=index_name,
                new_constituents=fresh,
                reason=f"Automated sync from {fetcher.__class__.__name__}"
            )
            return {
                "status": "ok",
                "index": index_name,
                "added": diff["added"],
                "removed": diff["removed"]
            }
        except Exception as e:
            logger.error(f"Sync failed for index {index_name}: {e}")
            return {
                "status": "error",
                "index": index_name,
                "message": str(e)
            }


_GLOBAL_UNIVERSE_MANAGER: Optional[DynamicUniverseManager] = None


def get_universe_manager(offline_mode: bool = True) -> DynamicUniverseManager:
    """Singleton getter for DynamicUniverseManager."""
    global _GLOBAL_UNIVERSE_MANAGER
    if _GLOBAL_UNIVERSE_MANAGER is None:
        _GLOBAL_UNIVERSE_MANAGER = DynamicUniverseManager(offline_mode=offline_mode)
    return _GLOBAL_UNIVERSE_MANAGER
