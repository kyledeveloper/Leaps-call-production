# Options Quant Strategy Scanner

Research scanner for **US Options Strategies** featuring dual strategy families: **LEAPS Call** (buyer stock-replacement, $DTE \ge 250$) and **Cash-Secured Put (CSP)** (systematic seller yield, $DTE\ 7–45$). It is **not** an auto-trader.

[English](README.md) | [简体中文](README.zh-CN.md)

## What it does

### 1. LEAPS Call Family (Buyer Strategies, $DTE \ge 250$)

| Board | Idea | Key Rules & Filters |
|---|---|---|
| **1. Deep ITM** | Stock-replacement / PMCC base | Strike 0.65S–0.85S, Δ 0.70–0.85 PASS, Δ > 0.90 skipped at ingest. Carry = extrinsic / (P_exec × T) + dividend yield drag. Spread & OI prioritized; volume requires ask_size ≥ 5 (not a one-vote veto). Daily θ / P_exec gated. |
| **2. Volatility discount** | Cheap vol, long vega | Real IV percentile after ≥90 stored ATM IV days. Delayed uses HV20 percentile until then and **caps at WATCH**. |
| **3. Oversold confluence** | Mean-reversion on core names | RSI / 200DMA / 52w / bounce score. Requires core-universe membership and ≥200 daily bars. |

### 2. Cash-Secured Put (CSP) Family (Seller Strategies, $DTE\ 7–45$)

| Board | Idea | Key Rules & Filters |
|---|---|---|
| **1. Premium Harvest** | Systematic annual yield harvest | $\Delta \in [-0.30, -0.15]$, DTE 7–45, ranked by Annualized Return on Capital (AROC). Zero-bid and spread-inversion veto. |
| **2. Wheel / Accumulation** | Value dip accumulation | $\Delta \in [-0.45, -0.30]$, downside buffer $\ge 5\%$, RSI $\le 55$, price-to-200DMA $\le 1.05$. |
| **3. High IV Rank** | Volatility crush & mean reversion | IV Rank $\ge 50\%$, $\Delta \in [-0.35, -0.15]$, capturing elevated implied volatility premiums. |

#### 🛡️ CSP Seller Risk Modeling & Controls
- **Execution Slippage ($P_{exec}$)**: Penalizes wide spreads and thin order books ($Bid + (1 - \alpha) \times (Ask - Bid) \times DepthFactor$). Zero-bid ($Bid \le 0$) is immediately rejected.
- **DTE < 7 Guard & Gamma Penalty**: Prevents terminal gamma explosion and AROC division-by-zero ($DTE_{safe} = \max(DTE, 7)$; 15% haircut for $DTE < 14$).
- **POP & Downside Buffer**: Delta-linear probability of profit ($1 - |\Delta|$) with disclaimer; buffer $(S - K) / S$.
- **Capital Allocation & Max Loss**: Computes nominal required collateral ($K \times 100$), maximum possible loss ($K \times 100 - P_{exec} \times 100$), and caps single-stock exposure at 25% of the total cash pool.
- **-15% Market Crash Stress Test**: Simulates net portfolio PnL under a sudden 15% underlying gap down.
- **Earnings Date Guard**: Flags earnings within DTE (`🚨 In DTE` or `⚠️ Unverified`).

## Data sources

| Mode | Feed | Keys | Description |
|---|---|---|---|
| **Delayed public (Default)** | Yahoo daily bars + Nasdaq OPRA delayed chains | None (~15 min delay, not NBBO) | Real market quotes (e.g., AVGO spot $339.51) without credentials. Automatically used by default. |
| **Live Webull** | Webull OpenAPI | App Key + Secret | Memory-only (never written to disk), optional live quotes. |
| **Sandbox (Test-only)** | Local deterministic fixtures | None | Hermetic offline sandbox used strictly in test suites (`--offline`). Hidden from user UI to prevent mock data leakage. |

Webull keys must not be committed or pasted into chat. `.env` is gitignored.

ATM IV from Delayed/Webull scans is appended to `data/iv_history.json` so strategy 2 can graduate from the HV proxy after ~90 trading days.

Delayed scans fetch ticker chains concurrently (configurable via `LEAPS_FETCH_WORKERS` / `LEAPS_FETCH_MIN_INTERVAL`). Yahoo daily bars are cached locally per trading session date (`data/daily_bars.json`, gitignored). LEAPS and CSP scan pipelines are strictly isolated to eliminate cross-strategy overhead.

## Run

Requires Python 3.9+.

```bash
cd Leaps-call-production
pip install -r requirements.txt

# Start the Web Scanner (defaults to Delayed public real market data)
python3 -m leaps_scanner.cli server --port 8000
```

Open the dashboard:
- Default dashboard: `http://localhost:8000/`
- Direct CSP deep-link: `http://localhost:8000/csp` or `?family=csp`

### CLI Scans (No UI)

```bash
# LEAPS Call scan (using live delayed market data)
python3 -m leaps_scanner.cli --family leaps --symbols SPY,QQQ --alpha 0.5
python3 -m leaps_scanner.cli --strategy deep_itm --universe etfs

# Cash-Secured Put scan (using live delayed market data)
python3 -m leaps_scanner.cli --family csp --symbols AAPL,MSFT,NVDA,AVGO
python3 -m leaps_scanner.cli --family csp --strategy csp_harvest --universe core

# (Optional) Forced offline sandbox fixtures for quick local testing
python3 -m leaps_scanner.cli --offline --family csp --symbols AAPL,SPY
```

### Dashboard Features
- **Strategy Switcher**: Toggle between LEAPS Call and Cash-Secured Put.
- **Dynamic In-Memory Re-ranking**: Adjust α slippage, cash pool, single-ticker exposure, or 6 filter pills (AROC, Buffer, IV Rank, POP, Earnings, Liquidity) instantly without network re-fetching.
- **Multi-language**: EN / 中文 toggle in the header (persisted in `localStorage`).
- **Universe Tiers**: **ETFs** (12) · **DJIA** (30) · **SP100** · **NDX** · **Core** (union).
- **Background Auto-Scan**: Server automatically scans market data in the background upon launch with live progress tracking.

## Tests

```bash
# Run all Python unit and hermetic edge-case tests (171 tests)
PYTHONPATH=src python3 -m unittest discover -s tests/python -q

# Run JavaScript toolchain and pre-push gatekeeper tests
npm test
```

All unit tests are hermetic: network sockets are blocked.

## Pricing & Greeks

- **American Option Pricing**: Bjerksund–Stensland (2002) model with exact Put-Call symmetry transformation ($AmericanPut(S, K, T, r, q, \sigma) = AmericanCall(K, S, T, q, r, \sigma)$).
- **Physical No-Arbitrage Bounds**: $K e^{-rT} \le EuropeanPut \le AmericanPut \le K$.
- **Model Greeks**: Analytic Black-Scholes / Bjerksund-Stensland Greeks. Put delta strictly bounded in $[-1.0, 0.0]$.
- **Execution Price**:
  - LEAPS: $P_{exec} = mid + \alpha \times (ask - mid)$
  - CSP: $P_{exec} = bid + (1 - \alpha) \times (ask - bid) \times DepthFactor$

Greeks are model estimates, not real-time exchange feeds. Treat PASS/WATCH as an analytical shortlist and verify on your broker terminal before executing.

## License

MIT. See [LICENSE](LICENSE).

