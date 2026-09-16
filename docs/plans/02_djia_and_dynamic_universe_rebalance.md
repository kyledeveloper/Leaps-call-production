# Implementation Plan: DJIA Universe Expansion & Automated Dynamic Rebalancing Engine

## Overview
This plan incorporates the **Dow Jones Industrial Average (DJIA / Dow 30)** full constituents into the LEAPS Call quantitative scanner universe and implements an **Automated Dynamic Index Rebalancing Engine (`DynamicUniverseManager`)** to detect, synchronize, and update stock pool constituents when S&P 100, Nasdaq 100, or DJIA undergo scheduled quarterly or ad-hoc rebalances.

---

## User Review Required

> [!IMPORTANT]
> **Key Architectural Standards**:
> 1. **Zero External Dependency**: Built using standard library `urllib.request` (with compliant User-Agent and strict 3s timeout) + `html.parser`.
> 2. **Canonical Symbology Pipeline (`SymbologyNormalizer`)**:
>    - Internal canonical representation uses dot format (e.g., `BRK.B`, `BF.B`).
>    - Egress transformation to broker format converts dot to hyphen (`to_broker("BRK.B", broker="webull")` -> `BRK-B`) to prevent 404 / empty chain silent failures.
> 3. **Mathematical Set Union Invariant**:
>    - `FULL_CORE_UNIVERSE = SP100 ∪ NASDAQ100 ∪ DJIA ∪ ETFS ∪ ADRS ∪ PINNED_HOLDINGS`.
>    - Dropping a constituent from DJIA (e.g., `INTC` replaced by `NVDA` in Nov 2024) **never** removes it from the master universe if it is still a member of S&P 100 or Nasdaq 100.
> 4. **Crash-Proof CloudStorage & Concurrency Safety**:
>    - File locks (`fcntl.flock` / `.lock`) prevent race conditions between concurrent CLI and Web API workers.
>    - Checksum validation (`sha256`); corrupted caches are automatically quarantined to `.corrupt.bak` and smoothly fall back to static seed data.
> 5. **100% Hermetic Offline Sandbox Guarantee**:
>    - Default `offline_mode=True`. Unit tests use offline HTML snapshots (`fixtures/wiki_*.html`) inside `tempfile.TemporaryDirectory()`, with zero external network access.

---

## Red-Team Adversarial Inquest & Defensive Clauses

In response to the formal 《方案红队质询函》(Design Adversarial Inquest), the Blue Team has integrated the following 5 mandatory defensive clauses into the architecture:

### Defensive Clause 1: Scraper Anti-Fragility & Strict Bidirectional Cardinality Gate
- **Compliant User-Agent & Timeout**: Wikimedia requires identified User-Agents. Requests include `User-Agent: LeapsScanner/1.0 (quant-contact@internal.lan; dev@leaps-call.local)` with a strict `timeout=3.0s`.
- **Table Signature Verification**: HTML parsing locates tables matching exact header signatures (`{"Symbol", "Company"}` or `{"Ticker", "Security"}`), rejecting irrelevant tables (e.g. historical former components or milestones).
- **Strict Token Sanitation Regex**: Extracted symbols pass `^[A-Z]{1,5}([.-][A-Z]{1,2})?$`. All footnote citations (e.g., `NVDA[1]`, `CRM\xa0*`) and exchange prefixes (`NYSE:`) are stripped.
- **Bidirectional Cardinality Gate**:
  - `DJIA`: Exactly `== 30`.
  - `SP100`: Must fall within `[98, 105]`.
  - `NASDAQ100`: Must fall within `[98, 105]`.
  If counts violate bounds, the remote payload is rejected, keeping the existing cache intact and raising an audit alert.

### Defensive Clause 2: Canonical Symbology Pipeline (`SymbologyNormalizer`)
- All incoming symbols from external feeds (Wikipedia, SEC, user CLI input) are normalized to **Canonical Format** (`BRK.B`, `BF.B`, `GOOG`, `GOOGL`).
- Outbound requests to `WebullClient` pass through `SymbologyNormalizer.to_broker(symbol, broker="webull")`, transforming `BRK.B` -> `BRK-B`.
- Unit tests verify round-trip transformations and option chain resolution for multi-class shares.

### Defensive Clause 3: Sub-Pool Independence & Dynamic Master Union
- Rebalance diffs are computed and stored strictly per sub-index: `indices["djia"]`, `indices["sp100"]`, `indices["nasdaq100"]`.
- `FULL_CORE_UNIVERSE` is computed dynamically as the mathematical union:
  $$\text{Master} = \bigcup_{i} \text{Index}_i \cup \text{ETFs} \cup \text{ADRs} \cup \text{Pinned}$$
- If a ticker is removed from one index but present in another (e.g., `INTC` removed from DJIA but in S&P 100 / Nasdaq 100), it remains active in Master.
- Pinned/Monitored positions receive a `REMOVED_GRACE_PERIOD` tag (90 days retention) to prevent orphaned open LEAPS contracts.

### Defensive Clause 4: Process File Lock, Cloud Drive Safety, & Self-Healing Cache
- Inter-process file locking ensures CLI `--sync-universe` and Web API `POST /api/universe/sync` serialize write operations without data corruption.
- CloudStorage-safe atomic write: writes to temporary file with flush/fsync before replacement.
- Cache integrity: Stores `sha256` checksum. If JSON is truncated, corrupt, or checksum mismatches, the system moves the damaged file to `.corrupt.bak` and seamlessly boots with static seed constituents.
- In-memory `AppState` checks `st_mtime` to hot-reload external updates without requiring server restarts.

### Defensive Clause 5: 100% Hermetic Sandbox Test Isolation
- All unit and integration tests run with `socket.socket.connect` blocked.
- Pre-recorded offline HTML fixtures (`tests/python/fixtures/wiki_djia.html`, `tests/python/fixtures/wiki_sp100.html`) are used to test the HTML parser.
- Disk cache read/write tests execute inside isolated temporary directories (`tempfile.TemporaryDirectory()`), ensuring zero side effects on production code repositories.

---

## Proposed Changes

### Core Architecture & Rebalance Engine

#### [MODIFY] [universe.py](file:///Users/haokunliang/Library/CloudStorage/GoogleDrive-liangkyle3@gmail.com/My%20Drive/Leaps-call-production/src/leaps_scanner/data/universe.py)
- Define authoritative `DJIA_COMPONENTS` (30 tickers, containing current components: AAPL, AMGN, AMZN, AXP, BA, CAT, CRM, CSCO, CVX, DIS, GS, HD, HON, IBM, JNJ, JPM, KO, MCD, MMM, MRK, MSFT, NKE, NVDA, PG, SHW, TRV, UNH, V, VZ, WMT).
- Update `FULL_CORE_UNIVERSE` to include DJIA (adds Sherwin-Williams `SHW` and Travelers `TRV`).
- Add `SymbologyNormalizer` (`to_canonical`, `to_broker`).
- Enhance `get_universe("djia")`.
- Export `get_universe_manager()` singleton with safe disk cache fallback.

#### [NEW] [rebalancer.py](file:///Users/haokunliang/Library/CloudStorage/GoogleDrive-liangkyle3@gmail.com/My%20Drive/Leaps-call-production/src/leaps_scanner/data/rebalancer.py)
- `ConstituentFetcher` interface with `WikipediaConstituentFetcher` (table header signature parser, compliant User-Agent, regex cleaner) and `LocalSeedFetcher`.
- `DynamicUniverseManager`:
  - Persistent JSON cache at `src/leaps_scanner/data/store/universe_cache.json`.
  - Cardinality gatekeepers (strict 30 for DJIA, [98, 105] for S&P/Nasdaq).
  - Mathematical set diff detector: `added = new - old`, `removed = old - new`.
  - Rebalance event logging with audit timestamps and reasons.
  - Process file locking (`fcntl.flock`) and sha256 checksum verification.
  - Cache self-healing: quarantine damaged files to `.corrupt.bak`.

---

### Broker & Scraper Integration

#### [MODIFY] [webull.py](file:///Users/haokunliang/Library/CloudStorage/GoogleDrive-liangkyle3@gmail.com/My%20Drive/Leaps-call-production/src/leaps_scanner/data/webull.py)
- Use `SymbologyNormalizer.to_broker(symbol, broker="webull")` when querying ticker option chains and quotes, ensuring `BRK.B` correctly resolves as `BRK-B`.

---

### CLI & API Integration

#### [MODIFY] [cli.py](file:///Users/haokunliang/Library/CloudStorage/GoogleDrive-liangkyle3@gmail.com/My%20Drive/Leaps-call-production/src/leaps_scanner/cli.py)
- Add `--universe djia` to choices (`sp100`, `nasdaq100`, `djia`, `etfs`, `adrs`, `all`).
- Add `--sync-universe` flag to execute a rebalance check and print an ASCII rebalance report.

#### [MODIFY] [server.py](file:///Users/haokunliang/Library/CloudStorage/GoogleDrive-liangkyle3@gmail.com/My%20Drive/Leaps-call-production/src/leaps_scanner/api/server.py)
- Expose `GET /api/universe`: Returns breakdown of constituent counts, last synced time, and recent rebalance logs.
- Expose `POST /api/universe/sync`: Triggers rebalance synchronization check and hot-reloads in-memory universe.

#### [MODIFY] [static/index.html](file:///Users/haokunliang/Library/CloudStorage/GoogleDrive-liangkyle3@gmail.com/My%20Drive/Leaps-call-production/src/leaps_scanner/api/static/index.html)
- Display live Universe Statistics chip (`SP100: 101 | NDX100: 101 | DJIA: 30 | Total: 160+`).
- Add "Check Rebalance" trigger button with status feedback.

---

### Verification Plan

### Automated Test Suites (Hermetic Sandbox)
1. `tests/python/test_universe.py`:
   - Verify `DJIA_COMPONENTS` has 30 tickers, including current Nov 2024 constituents (`NVDA`, `SHW`).
   - Test `get_universe("djia")` returns 30 sorted tickers.
   - Test `SymbologyNormalizer.to_canonical()` and `to_broker()`.
   - Test that multi-index overlap preserves tickers (e.g. removing from DJIA sub-pool does not remove from master pool if in SP100).
2. `tests/python/test_rebalancer.py`:
   - Test `WikipediaConstituentFetcher` parsing against offline fixtures (`fixtures/wiki_djia.html`, `fixtures/wiki_sp100.html`).
   - Test cardinality gates (reject table with <30 or >30 DJIA symbols).
   - Test regex token cleaner with footnote and whitespace noise (`NVDA[1]`, `CRM*`, `NYSE: UNH`).
   - Test rebalance detection (`added`, `removed`) and event recording.
   - Test process file lock and atomic cache write in `tempfile.TemporaryDirectory()`.
   - Test corrupt cache recovery: verify damaged JSON is quarantined and system seamlessly recovers to static seed.
   - Verify 100% offline pass with `socket.socket.connect` blocked.

### Manual Verification
- `python -m src.leaps_scanner.cli --universe djia`: Confirm DJIA scan executes and formats outputs.
- `python -m src.leaps_scanner.cli --sync-universe`: Confirm constituent diff detection and formatted report output.
- Open Web Dashboard at `http://localhost:8000`: Confirm live Universe stats and rebalance trigger functionality.
