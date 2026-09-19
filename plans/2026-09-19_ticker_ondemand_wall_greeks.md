# Ticker On-Demand Wall + Greeks + Term Sheet

**Status:** SPEC ONLY — do not change harvest/LEAPS scan filters.  
**Date:** 2026-09-19  
**Goal:** When the user opens a single ticker (`/chain`), fetch **one extra Nasdaq range query** (plus Yahoo only on cache miss) and render **new blocks above the existing chain page**. Existing chain data, `contracts[]`, accordion, filters, and gate diagnostics stay byte-compatible.

**Hard UI rule:** 现有 chain 页面数据不要变化，只把新块放在上面。 The current diagnostic table / expiry accordion / PASS-WATCH-REJECT toolbar / hero contract count must keep today’s payload and rendering. No new columns on that table. No unthinning of those rows.

English code, comments, tests, and commit messages. User-facing copy in `chain.html` i18n (EN + 中文). Update both `README.md` and `README.zh-CN.md`.

---

## 0. Why this change exists

Universe scan cost is **O(tickers)**, not O(contracts). One Nasdaq `option-chain?limit=0&fromdate&todate` already returns every expiry and strike in the window, including `c_Openinterest` and `p_Openinterest` on the same row.

The scan pipeline then **throws most of that away**:

- CSP keeps puts only, then `_select_csp_contracts` keeps a few strikes near moneyness targets.
- Greeks engine computes Δ, Γ, Θ, Vega, Rho via finite difference on Bjerksund–Stensland 2002, then **stores only Δ** (LEAPS Deep-ITM also uses `|Θ_day|/P_exec`).
- There is no option wall / max pain / GEX today.

Do **not** pull walls during Core/ETF scans. Pull them **on ticker click** only.

---

## 1. Non-goals (hard)

Antigravity MUST NOT:

1. Change CSP/LEAPS ingest filters, DTE buckets, harvest/wheel/vol-rank gates, or default pills.
2. Upsert the unthinned chain into `AppState.csp_candidates` or `AppState.candidates`. That would explode the harvest table with far OTM strikes.
3. Fetch walls for every symbol in a universe scan.
4. Touch `LEAPS_FETCH_WORKERS` / `LEAPS_FETCH_MIN_INTERVAL`.
5. Add ticker pre-filters (IV rank, earnings skip, “quality” names).
6. Add GEX, Vanna, Charm, real-time NBBO, or Webull-only fields.
7. Implement portfolio book construction, scan-to-scan memory, or main-board REJECT histograms (later).
8. `git push`.
9. **Change existing `/chain` data or UI below the insertion point.** Forbidden examples:
   - adding `Γ/Θ/Vega/Rho` columns to the current accordion table
   - replacing `contracts[]` with unthinned full-chain rows
   - changing `contract_count`, PASS/WATCH/REJECT tallies, `gate_diagnostics`, hero “合约总数”
   - altering `_evaluate_single_csp_diagnostics` / `_evaluate_single_leaps_diagnostics` field sets
   - changing family tabs, status pills, expiry grouping, refresh cooldown UX
   - using the raw dual-chain parser inside `get_csp_candidates` / `get_leaps_candidates`

---

## 2. Product behavior

### 2.1 Entry

Existing path, keep it:

- Main dashboard row / grouped ticker → `/chain.html?ticker=AAPL&family=csp&...`
- API: `GET /api/v1/ticker/chain`

Default open of `/chain` still uses **cached strategy contracts** if the last universe scan already has that ticker (no extra HTTP).

Wall + raw chain + extra Greeks load when:

- `contracts` for that ticker are missing, **or**
- user clicks **刷新单票全链** (`force_refresh=true`), **or**
- response has no `wall` yet (first visit after this feature): server may fetch raw chain even without `force_refresh` if the **raw-chain side cache** is cold. This is at most **one Nasdaq call per ticker per TTL**, not a scan.

### 2.2 Page layout (top → bottom)

Keep **verbatim** (do not restyle, reorder, or add columns):

- Hero (badge, spot, as-of, **合约总数 from existing `contract_count`**)
- Family tabs + strategy sub-tabs
- Filter bar: ALL / PASS / WATCH / REJECT, summary badges, expand/collapse
- `#chainContainer` expiry accordion and every column it has today

Insert **one new wrapper** `#ondemandPanel` **between** `#warningBanner` / tabs **and** `#filterBar` (i.e. physically **above** the existing chain table, not inside it):

```
hero
tabs
★ NEW: disclaimer + term sheet + wall   ← only addition
filter bar
existing accordion                      ← unchanged renderer
```

If `wall` is null, hide `#ondemandPanel`; existing page looks as it does now.

**A. Disclaimer strip** (inside the new panel only)  
EN: `Open interest is delayed (typically T+1). Walls are overnight positioning, not a live gamma wall.`  
ZH: `持仓量为延迟数据（多为 T+1）。墙位反映隔夜开仓分布，不是盘中 Gamma 墙。`

**B. Term sheet — 4 DTE buckets in one row**  
Buckets: `<7`, `7-14`, `14-28`, `28-45` (same edges as `get_dte_bucket` in `csp_ranker.py`, including aliases `0-7` / `29-45`).

This grid is **not** the accordion. Greeks shown here do not appear as new columns below.

Each cell (or `—` if empty):

| Field | Source |
|---|---|
| DTE label | `formatCspDte` style, e.g. `3.1d` |
| Expiry | ISO date |
| Strike | representative put (CSP) or call (LEAPS) |
| Δ | stored greek |
| Bid / Ask / mid | chain |
| AROC | CSP only; LEAPS: carry or omit |
| Buffer | CSP |
| OI | that contract |
| Status | PASS/WATCH/REJECT against **current family strategy** |

Representative pick (deterministic, test this):

1. Filter unthinned options of the right type (put if `family=csp`, call if `family=leaps`) in bucket.
2. Prefer strike closest to harvest-zone moneyness if CSP: `0.97` for `<7` and `7-14`, `0.93` for `14-28`, `0.92` for `28-45`. If LEAPS: closest to `0.75 * spot` (deep ITM call band).
3. Tie-break: higher OI, then tighter spread.

**C. Option wall**

- Horizontal bar chart (CSS/flex or SVG, no new JS chart library).
- X = strike, Y = OI.
- Two series: **Call OI** (one color) and **Put OI** (another). Spot as a vertical marker.
- Default aggregation: **all expiries in the wall window summed by strike**.
- Toggle: `All 0–45D` (default) vs **one expiry** (dropdown of expiries that have OI).
- Call-out chips:
  - `Call wall` = strike with max call OI
  - `Put wall` = strike with max put OI
  - `Max pain` = strike minimizing `sum_K [ call_oi(K)*max(S-K,0) + put_oi(K)*max(K-S,0) ]` using **spot S** as the assumed expiry price grid on listed strikes only
- Empty: `No OI in window` — do not fake zeros.

**D. Existing diagnostic table — DO NOT TOUCH**

- Same `renderData()` accordion path, same columns, same `contracts` array.
- Do **not** add greek columns, display caps, or unthinned rows to it.
- `totalContractsCount` continues to equal `contracts.length` from the old payload, not `raw_put_count`.

### 2.3 Family rules

| Family | New panel (wall + term sheet) | Existing `contracts[]` / accordion |
|---|---|---|
| `csp` | One Nasdaq 0–45 dual-chain (call+put OI) | **Unchanged:** thinned scan/diagnostic puts via today’s `get_csp_candidates` + `_evaluate_single_csp_diagnostics` |
| `leaps` | Same 0–45 wall fetch (extra Nasdaq) | **Unchanged:** today’s 250+ call diagnostics |
| `cc` | Panel hidden | Unchanged blueprint 200 |

CSP click = 1 extra Nasdaq for the **new panel only**. Existing contracts still come from the scan cache (or today’s force_refresh thinned refetch).  
LEAPS click, cold wall cache = 1 extra 0–45 Nasdaq for the panel; LEAPS accordion fetch path unchanged.

Yahoo chart: only if daily-bar cache misses. Never block the old accordion if wall/Yahoo fails.

---

## 3. Isolation and caching

### 3.1 Side cache (required)

New in-memory map on `AppState`, **not** mixed with scan candidates:

```python
# key: f"{ticker}|wall|{session_date}"
self._ticker_raw_cache: Dict[str, RawChainSnapshot]
```

`RawChainSnapshot` (frozen dataclass, new module or `csp_ranker.py` sibling):

- `ticker`, `spot`, `asof_iso`, `window_from`, `window_to`
- `rows: List[RawOptionRow]`  # both calls and puts, unthinned
- `fetched_at: float`

TTL: reuse `LEAPS_DAILY_BAR_CACHE_TTL` default **3600s**, or dedicated `LEAPS_RAW_CHAIN_TTL` default **900s** (15 min). Env override. Session date from `market_session_date()`.

`force_refresh=true` bypasses TTL subject to **existing** per-ticker 15s cooldown and global 30 / window limiter. On cooldown, return cached wall if present and `cooldown_active: true`.

**Never** write `rows` into `csp_candidates`.

Optional disk persist: **skip for v1** (in-memory only). Process restart just refetches on next click.

### 3.2 Scan pipeline

`get_csp_candidates` / `get_leaps_candidates` / `_select_csp_candidates` / `_select_csp_contracts`: **no behavior change**. Add tests that hash or count selected strikes so a regression fails if someone “reuses” raw fetch inside the scan.

---

## 4. Data and parsing

### 4.1 New parse: both sides, no strategy filter

Today `parse_nasdaq_csp_chain` drops calls and drops `bid<=0`. Wall OI must **keep zero-bid rows** if OI > 0 (a wall is positioning, not a quote).

Add `parse_nasdaq_dual_chain(payload, min_dte, max_dte, asof) -> List[RawOptionRow]`.

`RawOptionRow` fields (English names):

```
underlying, expiry, dte, strike, cp  # "C" | "P"
bid, ask, last, open_interest, volume, bid_size, ask_size
symbol  # OCC
```

- Call fields: `c_Bid`, `c_Ask`, `c_Openinterest`, `c_Volume`, …
- Put fields: `c_Bid` → `p_*`
- Strike from `strike` or OCC URL (`parse_occ_from_nasdaq_url`)
- Expiry from OCC URL, then `putExpiryDate` / `expirygroup` (already handled)
- **Include** rows with `oi>0` even if bid/ask missing or zero. Set bid/ask to `None` not `0` when missing (do not invent last as bid — DC-CSP-6).
- Exclude rows with no strike or no expiry.
- DTE same 20:00 UTC expiry convention as CSP parser (`max(0.05, raw_dte)` near 0DTE).

Keep existing parsers untouched for the scan path.

### 4.2 Fetch

`PublicDelayedClient.get_raw_dual_chain(symbol, min_dte=0.0, max_dte=45.05, should_stop=None) -> RawChainSnapshot`

Reuse `_nasdaq_csp_chain` window helper `csp_nasdaq_date_window` and assetclass fallback (`etf` vs `stocks`). Prefer **one range query**. Per-expiry fallback only if range returns zero rows (same as now). Still goes through `_get_json` + global `RateLimiter`.

Do **not** call `_select_csp_contracts` here.

### 4.3 Greeks — term sheet / wall panel only

Do **not** attach extra greeks onto `contracts[]`.

Compute IV + Δ/Γ/Θ/Vega/Rho only for the **four term-sheet representative rows** (cheap). Wall aggregation uses OI only, no greeks.

- IV: `solve_implied_volatility` (calls) or `solve_american_put_iv` (puts)
- Greeks: `calculate_american_greeks` / `calculate_american_put_greeks`
- Units already in `greeks.py`: Vega per 1 vol point, Rho per 1% rate, `theta_daily = theta/365.25`
- If IV/greeks fail: delta fallback (existing helpers), other greeks `null` on that term-sheet cell

No `LEAPS_DIAG_MAX_GREEKS_PER_EXPIRY` cap on the accordion — the accordion is not getting greeks.

### 4.4 Wall aggregation (`src/leaps_scanner/scoring/option_wall.py`)

Pure functions, no HTTP.

```python
def aggregate_walls(rows: List[RawOptionRow], spot: float) -> Dict[str, Any]
```

Return:

```json
{
  "spot": 220.15,
  "call_wall": {"strike": 230.0, "oi": 41200},
  "put_wall": {"strike": 210.0, "oi": 38800},
  "max_pain": {"strike": 215.0, "pain": 1234567.8},
  "by_strike": [{"strike": 200.0, "call_oi": 1, "put_oi": 2, "call_volume": 0, "put_volume": 0}],
  "by_expiry": [{"expiry": "2026-09-25", "dte": 6.1, "call_oi": 1, "put_oi": 2}],
  "oi_asof_note": "delayed_t1"
}
```

- `by_strike` sorted by strike ascending.
- Skip strikes where call_oi=put_oi=0.
- Max pain: evaluate listed strikes only; if no OI, `max_pain` is `null`.
- Ties for call/put wall: **lower strike wins** (stable).

---

## 5. API contract

Extend `GET /api/v1/ticker/chain` (and `/api/ticker/chain`).

Query (existing + new, all optional except ticker):

| Param | Default | Notes |
|---|---|---|
| `ticker` | required | existing canonicalization + `TICKER_REGEX` |
| `family` | `leaps` | `csp` / `leaps` / `cc` |
| `strategy` | family default | unchanged |
| `alpha`, `cash_pool` | existing | unchanged |
| `force_refresh` | false | existing limiter |
| `include_wall` | **true** | `false` restores old payload shape besides new nulls |
| `wall_expiry` | omitted | if ISO date, wall `by_strike` is that expiry only; chips recomputed |

**Backward compatible (must pass snapshot tests):** keep `status, ticker, spot, family, strategy, alpha, cash_pool, cooldown_active, contract_count, contracts, asof, warning` with the **same `contracts[]` item schema** as today (`symbol, underlying, strike, spot, dte, bid, ask, p_exec, delta, aroc, buffer, pop, status, reasons, gate_diagnostics`, plus whatever LEAPS items already have). Do not add `gamma` / `theta_daily` / `vega` / `rho` / `cp` onto those objects in v1.

Add **sibling** keys only:

```json
{
  "wall": { "...aggregate_walls..." } | null,
  "term_sheet": {
    "<7": { "expiry", "dte", "strike", "cp", "delta", "gamma", "theta_daily", "vega", "bid", "ask", "oi", "status", "aroc", "buffer" } | null,
    "7-14": null,
    "14-28": null,
    "28-45": null
  },
  "raw_call_count": 0,
  "raw_put_count": 0
}
```

`contract_count` remains `len(contracts)` of the **old** list, never raw chain length.

Isolation: `GET /api/v1/csp/boards` and `contracts` for that ticker must equal pre-click values (test this). `include_wall=false` → `wall`/`term_sheet` null, rest identical to current production.

Errors: keep 400 invalid ticker, 429 global refresh quota. Wall fetch failure → `wall: null`, `warning: "wall_fetch_failed: ..."`, still return strategy contracts from scan cache if any (200).

---

## 6. UI (`src/leaps_scanner/api/static/index.html` + `chain.html`)

- `index.html`: existing click-through to `/chain.html?ticker=...` — keep. No wall on the main table.
- `chain.html`:
  - Inject `#ondemandPanel` **above** `#filterBar` / `#chainContainer`. Do not edit the accordion HTML/JS that maps `data.contracts`.
  - Prefer: `renderOndemandPanel(data)` then existing `renderData()` unchanged.
  - i18n keys for wall, max pain, delayed OI note, term sheet only.
  - CSS: match existing tokens. Mobile 390px: term sheet 2×2, wall chart internally scrollable, **no page-level overflow**.
  - No Chart.js / D3. CSS bars or inline SVG.
  - Wall drawing: strikes within ±20% of spot; chips still use full data. Note `Showing strikes within ±20% of spot` if truncated.
  - Refresh button: still 15s cooldown; still refreshes **existing** contracts the old way; additionally refreshes wall cache. If wall fails, accordion still updates as today.

---

## 7. Files to touch (blast radius)

| File | Change |
|---|---|
| `src/leaps_scanner/data/public_delayed.py` | `RawOptionRow`, `parse_nasdaq_dual_chain`, `get_raw_dual_chain`. Do not alter scan selectors. |
| `src/leaps_scanner/scoring/option_wall.py` | **New.** Aggregation + max pain + term-sheet picker. |
| `src/leaps_scanner/core/greeks.py` | No logic change unless a tiny helper to dict-serialize. Prefer not to edit. |
| `src/leaps_scanner/api/server.py` | Side cache; `get_ticker_chain_diagnostics` orchestration; **stop upserting force-refresh raw chains into scan lists**. Strategy-only refresh may still upsert **thinned** `get_csp_candidates` results as today. Wall fetch is a separate call. |
| `src/leaps_scanner/api/static/chain.html` | New `#ondemandPanel` above the filter bar only. Accordion renderer untouched. |
| `README.md` / `README.zh-CN.md` | One short subsection: ticker drill-down walls are on-demand, OI delayed, scan filters unchanged. |
| Tests listed in §8 | New + regression |

Do not edit harvest strategy modules.

If the repo requires `node src/agent/index.js plan` before `src/` edits, run it with `--target src/leaps_scanner/api/server.py` (and the other src files) and proceed only if not `BREAKING`.

---

## 8. TDD (write these first, they must fail, then implement)

All tests hermetic (no real Nasdaq). Inject `fetch_fn` / raw payload fixtures.

### 8.1 `tests/python/test_option_wall.py`

1. Dual parser keeps call **and** put OI on the same strike; zero bid with OI>0 is kept for wall, not used as executable bid.
2. Dual parser does not substitute `p_Last` into bid (DC-CSP-6).
3. `aggregate_walls` call/put wall strikes + tie → lower strike.
4. Max pain picks the listed strike that minimizes the formula; empty OI → `null`.
5. Term-sheet picker returns four keys; missing bucket is `null`; CSP uses 0.97/0.93/0.92 targets as specified.
6. DTE bucket edges match `get_dte_bucket` (7.0 is `7-14` not `<7`, 28.0 is `28-45` — **use the existing function**, do not fork edges).

### 8.2 `tests/python/test_ticker_chain_api.py` (extend)

1. `include_wall=true` offline: wall object present from fixture raw cache, `csp_candidates` length unchanged.
2. Force refresh of wall does **not** append unthinned rows into `state.csp_candidates`.
3. `GET /api/v1/csp/boards` n-count for that ticker identical before vs after wall fetch.
4. Cooldown: second `force_refresh` within 15s sets `cooldown_active` and does not call fetch_fn again.
5. `family=cc` still 200 blueprint, `wall: null`.
6. Invalid ticker still 400.
7. Existing `contracts[]` keys/count/order/status match a frozen fixture after wall is added (`include_wall` true vs false).
8. `include_wall=false` omits or nulls `wall` and `term_sheet`; remainder identical to current production.
9. Do **not** assert `gamma` on `contracts[]` items.

### 8.3 `tests/python/test_csp_dte_coverage.py`

Add one regression: `_select_csp_contracts` output length on a fat fixture is unchanged (scan still thins). Dual parser is **not** used inside `_scan_csp_symbol`.

### 8.4 JS

If `test/ui-filter-search.test.js` pattern exists, add a small chain-page test **or** a pure function test if wall rendering helpers are extracted. Do not block on Playwright if hermetic JS can assert HTML builder output.

---

## 9. Implementation order (Red-Green)

1. Fixtures: minimal Nasdaq-shaped JSON with two expiries, several strikes, mixed zero-bid + live quotes, call and put OI.
2. Red: §8.1 parser + wall tests.
3. Green: `parse_nasdaq_dual_chain` + `option_wall.py`.
4. Red: API isolation tests (§8.2.1–3).
5. Green: `get_raw_dual_chain` + server side cache + payload fields; **fix upsert** so wall rows never enter scan lists.
6. Greeks on **term-sheet cells only**; do not mutate diagnostic contract dicts.
7. `chain.html`: new panel above filter bar; leave accordion renderer untouched.
8. README both languages.
9. Run: `PYTHONPATH=src python3 -m unittest tests.python.test_option_wall tests.python.test_ticker_chain_api tests.python.test_csp_dte_coverage tests.python.test_csp_api -q`

---

## 10. Copy / semantics notes

- Delayed public feed ≈ 15 minutes on quotes; **OI is worse (T+1)**. Never label wall as “gamma wall”.
- POP remains `1-|Δ|` with existing disclaimer; not a greek.
- Do not rank harvest from wall strikes. Wall is diagnostic.
- SPY/QQQ payloads can be multi-MB; only on click; cap greeks; cap chart to ±20% spot.

---

## 11. Acceptance checklist

- [ ] Universe scan QPS unchanged (same Nasdaq count per ticker as before).
- [ ] Click AAPL on CSP: **above** the old table — wall, put/call wall chips, max pain, 4-bucket term sheet, delayed-OI note.
- [ ] Below: accordion, columns, counts, PASS/WATCH/REJECT **look and data match pre-change**.
- [ ] Harvest main table row count for AAPL unchanged after opening chain.
- [ ] `contracts` JSON schema unchanged (`include_wall=true` vs `false` same list).
- [ ] Refresh button still 15s cooldown; no 429 storm from one user.
- [ ] LEAPS family: 0–45 wall still appears; LEAPS accordion still 250+ DTE calls.
- [ ] Mobile: no horizontal page overflow; wall scrolls internally.
- [ ] Offline/sandbox tests 100% hermetic.
- [ ] EN / 中文 strings for new UI.
- [ ] `git commit` locally only, message like: `feat(chain): on-demand option wall, greeks, and DTE term sheet`

---

## 12. Later (explicitly not this PR)

- Main-board per-ticker term sheet without click
- Book-level capital allocation across PASS names
- Scan-to-scan PASS memory
- REJECT-reason histogram
- Disk cache for raw chains
- Capacity-based ticker pre-filter
