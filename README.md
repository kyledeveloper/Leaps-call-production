# LEAPS Call Quant Scanner

Research scanner for **US far-dated call options** (DTE ≥ 250). It ranks contracts across three independent boards. It is **not** an auto-trader.

[English](README.md) | [简体中文](README.zh-CN.md)

## What it does

| Board | Idea | Notes |
|---|---|---|
| **1. Deep ITM** | Stock-replacement / PMCC base | Strike 0.65S–0.85S, Δ 0.70–0.85 PASS, Δ > 0.90 skipped at ingest. Carry = extrinsic / (P_exec × T). Daily θ / P_exec gated. |
| **2. Volatility discount** | Cheap vol, long vega | Real IV percentile after ≥90 stored ATM IV days. Until then Delayed uses HV20 percentile and **caps at WATCH**. |
| **3. Oversold confluence** | Mean-reversion on core names | RSI / 200DMA / 52w / bounce score. Needs core-universe membership and ≥200 daily bars. |

Unusual-flow (old strategy 4) was removed: delayed volume is too weak to use as a signal.

## Data sources

| Mode | Feed | Keys |
|---|---|---|
| **Sandbox** | Local fixtures | None |
| **Delayed public** | Yahoo daily bars + Nasdaq OPRA delayed chains | None (~15 min delay, not NBBO) |
| **Live Webull** | Webull OpenAPI | App Key + Secret, memory-only (never written to disk) |

Webull keys must not be committed or pasted into chat. `.env` is gitignored.

ATM IV from Delayed/Webull scans is appended to `data/iv_history.json` so strategy 2 can graduate from the HV proxy after ~90 trading days.

Delayed scans pull names in parallel (default 8 workers, 0.12s global spacing; `LEAPS_FETCH_WORKERS` / `LEAPS_FETCH_MIN_INTERVAL`). Yahoo daily bars are reused until the US session date rolls (`data/daily_bars.json`, gitignored). Option chains are still fetched each scan.

## Run

Python 3.9+.

```bash
cd Leaps-call-production
pip install -r requirements.txt
PYTHONPATH=. python -m src.leaps_scanner.cli --offline --serve --port 8000
```

Open the dashboard, then pick Sandbox / Delayed public / Live Webull.

CLI scan (no UI):

```bash
PYTHONPATH=. python -m src.leaps_scanner.cli --offline --symbols SPY,QQQ --alpha 0.5
PYTHONPATH=. python -m src.leaps_scanner.cli --strategy deep_itm --universe etfs
```

Dashboard: α slider re-ranks in memory (no network). Status filter defaults to PASS + WATCH. EN / 中文 toggle is in the header (saved in `localStorage`).

Scan universe tiers: **ETFs** (12) · **DJIA** (30) · **SP100** · **NDX** · **Core** (union). Delayed scans run in the background.

## Tests

```bash
PYTHONPATH=. python -m unittest discover -s tests/python -q
```

Tests are hermetic: sockets are blocked. Do not rely on live Yahoo/Nasdaq/Webull in CI.

## Deploy

This is a **long-running Python process** (in-memory ranker + background Delayed scan). Do **not** publish to Vercel, Netlify, Cloudflare Pages, or Grok Publish.

Practical setups:

- Always-on Mac mini + [Tailscale](https://tailscale.com/pricing) (Personal is $0) to open the dashboard from the office
- Small VPS ($4–6/mo) if you need a public HTTPS URL (put a password in front of it)

No database is required for personal use. Strategy 2’s IV file is local JSON.

## Pricing / Greeks

American calls use Bjerksund–Stensland 2002. Execution price:

`P_exec = mid + α × (ask − mid)` (α = 0.5 by default)

Delta / theta are model Greeks, not exchange Greeks. Treat PASS as a shortlist, then confirm on a broker tape before trading.

## License

MIT. See [LICENSE](LICENSE).
