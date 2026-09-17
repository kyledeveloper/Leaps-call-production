"""
Command Line Interface for LEAPS Call Quant Scanner.
Outputs formatted financial strategy boards to terminal and supports launching web dashboard.
"""
import argparse
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

# Ensure repository root is on sys.path for direct script execution
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.leaps_scanner.api.server import AppState, run_server
from src.leaps_scanner.data.universe import get_universe, SymbologyNormalizer
from src.leaps_scanner.data.rebalancer import get_universe_manager


def format_ascii_table(title: str, headers: List[str], rows: List[List[str]]) -> str:
    """Format headers and rows into a clean ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            if idx < len(col_widths):
                col_widths[idx] = max(col_widths[idx], len(str(cell)))

    sep_line = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_line = "|" + "|".join(f" {headers[i]:<{col_widths[i]}} " for i in range(len(headers))) + "|"

    output_lines = [
        f"\n=== {title} ===",
        sep_line,
        header_line,
        sep_line
    ]

    if not rows:
        empty_line = "| " + "No opportunities found".center(sum(col_widths) + len(col_widths) * 3 - 3) + " |"
        output_lines.append(empty_line)
    else:
        for row in rows:
            row_cells = []
            for i in range(len(headers)):
                val = str(row[i]) if i < len(row) else ""
                row_cells.append(f" {val:<{col_widths[i]}} ")
            output_lines.append("|" + "|".join(row_cells) + "|")

    output_lines.append(sep_line)
    return "\n".join(output_lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LEAPS Call Quantitative Scanner CLI")
    parser.add_argument("--symbols", type=str, default="SPY,QQQ,AAPL,NVDA", help="Comma-separated ticker symbols")
    parser.add_argument("--universe", type=str, default=None, choices=["sp100", "nasdaq100", "ndx", "npx", "oex", "djia", "etfs", "adrs", "all"], help="Predefined universe: sp100, nasdaq100, ndx, npx, oex, djia, etfs, adrs, all")
    parser.add_argument("--sync-universe", action="store_true", help="Check remote sources and sync index constituents")
    parser.add_argument("--family", type=str, default="leaps", choices=["leaps", "csp"], help="Strategy family: leaps (LEAPS Call) or csp (Cash-Secured Put)")

    parser.add_argument("--cash-pool", type=float, default=50000.0, help="Available cash pool for CSP sizing (default: $50,000)")
    parser.add_argument("--max-capital", type=float, default=None, help="Max nominal capital per contract for CSP")
    parser.add_argument("--min-aroc", type=float, default=0.12, help="Minimum AROC for CSP filter (default: 0.12)")
    parser.add_argument("--min-buffer", type=float, default=0.03, help="Minimum Downside Buffer for CSP filter (default: 0.03)")
    parser.add_argument("--alpha", type=float, default=0.5, help="Execution slippage alpha in [0.0, 1.0]")
    parser.add_argument("--strategy", type=str, default="all", choices=["all", "deep_itm", "vol_discount", "oversold", "harvest", "wheel", "vol_rank"], help="Strategy filter")
    parser.add_argument("--serve", action="store_true", help="Launch interactive Web Dashboard HTTP server")
    parser.add_argument("--port", type=int, default=8000, help="Web Dashboard port")
    parser.add_argument("--offline", action="store_true", default=False, help="Force offline sandbox mode (default: online/live data)")

    args = parser.parse_args(argv)


    if args.serve:
        run_server(port=args.port, offline_mode=args.offline)
        return 0

    if args.sync_universe:
        manager = get_universe_manager(offline_mode=args.offline)
        print("=" * 80)
        print("🔄 INDEX CONSTITUENTS DYNAMIC REBALANCE SYNC")
        print("=" * 80)
        for idx in ["djia", "sp100", "nasdaq100"]:
            res = manager.sync_index(idx)
            status = res.get("status")
            if status == "ok":
                added = res.get("added", [])
                removed = res.get("removed", [])
                print(f"[{idx.upper():<9}] Status: OK | Added ({len(added)}): {', '.join(added) if added else 'None'} | Removed ({len(removed)}): {', '.join(removed) if removed else 'None'}")
            else:
                print(f"[{idx.upper():<9}] Status: FAILED | {res.get('message')}")
        print("=" * 80)
        return 0

    if args.universe:
        symbols = get_universe(args.universe)
    else:
        symbols = [SymbologyNormalizer.to_canonical(s.strip()) for s in args.symbols.split(",") if s.strip()]

    alpha = max(0.0, min(1.0, args.alpha))

    state = AppState(offline_mode=args.offline)
    state.run_scan(symbols=symbols)
    if args.family == "csp":
        from src.leaps_scanner.scoring.csp_ranker import CSPFilterConfig
        cfg = CSPFilterConfig(
            min_aroc=args.min_aroc,
            min_buffer=args.min_buffer,
            max_capital_per_contract=args.max_capital
        )
        csp_boards = state.get_csp_boards(alpha=alpha, config=cfg, cash_pool=args.cash_pool)
        print("=" * 80)
        print(f"🛡️ CASH-SECURED PUT QUANT SCANNER (7~45 DTE) | Execution α = {alpha:.2f} | Cash Pool = ${args.cash_pool:,.0f}")
        print(f"Symbols: {', '.join(symbols)} | Mode: {'SANDBOX OFFLINE' if args.offline else 'ONLINE'}")
        print("=" * 80)

        def _fmt_st(s: Any) -> str:
            if hasattr(s, "value"):
                return str(s.value)
            return str(s).replace("GuardStatus.", "")

        def _fmt_sym(cand: dict) -> str:
            sym = cand["symbol"]
            est = cand.get("earnings_status", "")
            if est == "EARNINGS_UNVERIFIED":
                return f"{sym} [⚠️Unverified]"
            elif est == "EARNINGS_IMPACTED":
                return f"{sym} [🚨In DTE]"
            return sym

        # Harvest Board
        if args.strategy in ("all", "harvest"):
            h_items = csp_boards.get("harvest", [])
            h_headers = ["Symbol", "Strike", "Spot", "DTE", "Delta", "P_exec", "ROC / AROC", "Buffer", "POP(Δ)", "Contracts", "Max Loss", "Stress -15%", "Status"]
            h_rows = [
                [
                    _fmt_sym(it["candidate"]),
                    f"${it['candidate']['strike']:.1f}",
                    f"${it['candidate']['spot']:.1f}",
                    f"{int(it['candidate']['dte'])}d",
                    f"{it['candidate']['delta']:.2f}",
                    f"${it['p_exec']:.2f}",
                    f"{(it.get('roc', 0.0) * 100):.1f}% / {(it['aroc'] * 100):.1f}%",
                    f"{(it['buffer'] * 100):.1f}%",
                    f"{(it['pop'] * 100):.1f}%",
                    f"{it['capital_info']['recommended_contracts']}",
                    f"${it['capital_info']['total_max_loss']:,.0f}",
                    f"${it.get('stress_pnl', 0.0):,.0f}",
                    _fmt_st(it["status"])
                ]
                for it in h_items
            ]
            print(format_ascii_table("CSP Board 1: Premium Harvesting (High AROC, OTM)", h_headers, h_rows))

        # Wheel Board
        if args.strategy in ("all", "wheel"):
            w_items = csp_boards.get("wheel", [])
            w_headers = ["Symbol", "Strike", "Spot", "DTE", "Delta", "P_exec", "ROC / AROC", "Buffer", "POP(Δ)", "Contracts", "Max Loss", "Stress -15%", "Status"]
            w_rows = [
                [
                    _fmt_sym(it["candidate"]),
                    f"${it['candidate']['strike']:.1f}",
                    f"${it['candidate']['spot']:.1f}",
                    f"{int(it['candidate']['dte'])}d",
                    f"{it['candidate']['delta']:.2f}",
                    f"${it['p_exec']:.2f}",
                    f"{(it.get('roc', 0.0) * 100):.1f}% / {(it['aroc'] * 100):.1f}%",
                    f"{(it['buffer'] * 100):.1f}%",
                    f"{(it['pop'] * 100):.1f}%",
                    f"{it['capital_info']['recommended_contracts']}",
                    f"${it['capital_info']['total_max_loss']:,.0f}",
                    f"${it.get('stress_pnl', 0.0):,.0f}",
                    _fmt_st(it["status"])
                ]
                for it in w_items
            ]
            print(format_ascii_table("CSP Board 2: Wheel / Dip-Buying (Buffer & Oversold)", w_headers, w_rows))

        # Vol Rank Board
        if args.strategy in ("all", "vol_rank"):
            v_items = csp_boards.get("vol_rank", [])
            v_headers = ["Symbol", "Strike", "Spot", "DTE", "Delta", "IV Rank", "P_exec", "ROC / AROC", "Buffer", "POP(Δ)", "Contracts", "Max Loss", "Stress -15%", "Status"]
            v_rows = [
                [
                    _fmt_sym(it["candidate"]),
                    f"${it['candidate']['strike']:.1f}",
                    f"${it['candidate']['spot']:.1f}",
                    f"{int(it['candidate']['dte'])}d",
                    f"{it['candidate']['delta']:.2f}",
                    f"{(it['candidate']['iv_rank'] * 100):.1f}%" if it['candidate'].get('iv_rank') is not None else "N/A",
                    f"${it['p_exec']:.2f}",
                    f"{(it.get('roc', 0.0) * 100):.1f}% / {(it['aroc'] * 100):.1f}%",
                    f"{(it['buffer'] * 100):.1f}%",
                    f"{(it['pop'] * 100):.1f}%",
                    f"{it['capital_info']['recommended_contracts']}",
                    f"${it['capital_info']['total_max_loss']:,.0f}",
                    f"${it.get('stress_pnl', 0.0):,.0f}",
                    _fmt_st(it["status"])
                ]
                for it in v_items
            ]
            print(format_ascii_table("CSP Board 3: High IV Rank Harvest (Volatility Premium)", v_headers, v_rows))

        return 0

    boards = state.get_boards(alpha=alpha)

    print("=" * 80)
    print(f"🦅 LEAPS CALL QUANT SCANNER (DTE >= 250d) | Execution α = {alpha:.2f}")
    print(f"Symbols: {', '.join(symbols)} | Mode: {'SANDBOX OFFLINE' if args.offline else 'ONLINE'}")
    print("=" * 80)


    # 1. Deep ITM Board
    if args.strategy in ("all", "deep_itm"):
        items = boards.get("deep_itm", [])
        headers = ["Symbol", "Underlying", "Strike", "DTE", "P_exec", "Delta", "Eff.Lev", "Intrinsic%", "Carry/yr", "Status"]
        rows = [
            [
                it["symbol"],
                it["underlying"],
                f"${it['strike']:.1f}",
                f"{int(it['dte'])}d",
                f"${it['p_exec']:.2f}",
                f"{it['delta']:.2f}",
                f"{it['effective_leverage']:.1f}x",
                f"{(it['intrinsic_per_share'] / it['p_exec'] * 100):.1f}%" if it['p_exec'] > 0 else "0.0%",
                f"{(it['carry_cost'] * 100):.2f}%",
                it["status"]
            ]
            for it in items
        ]
        print(format_ascii_table("Strategy 1: Deep ITM Stock Replacement", headers, rows))

    # 2. Vol Discount Board
    if args.strategy in ("all", "vol_discount"):
        items = boards.get("vol_discount", [])
        headers = ["Symbol", "Underlying", "Strike", "DTE", "P_exec", "IV Pct", "Regime", "Carry/yr", "Status"]
        rows = [
            [
                it["symbol"],
                it["underlying"],
                f"${it['strike']:.1f}",
                f"{int(it['dte'])}d",
                f"${it['p_exec']:.2f}",
                f"{(it['iv_percentile'] * 100):.1f}%" if it.get("iv_percentile") is not None else "N/A",
                it.get("regime", "NONE"),
                f"{(it['carry_cost'] * 100):.2f}%",
                it["status"]
            ]
            for it in items
        ]
        print(format_ascii_table("Strategy 2: Volatility Discount", headers, rows))

    # 3. Oversold Board
    if args.strategy in ("all", "oversold"):
        items = boards.get("oversold", [])
        headers = ["Symbol", "Underlying", "Strike", "DTE", "P_exec", "Score", "Carry/yr", "Status"]
        rows = [
            [
                it["symbol"],
                it["underlying"],
                f"${it['strike']:.1f}",
                f"{int(it['dte'])}d",
                f"${it['p_exec']:.2f}",
                f"{it.get('confluence_score', 0.0):.1f}/4.0",
                f"{(it['carry_cost'] * 100):.2f}%",
                it["status"]
            ]
            for it in items
        ]
        print(format_ascii_table("Strategy 3: Blue-Chip Oversold Confluence", headers, rows))

    return 0


if __name__ == "__main__":
    sys.exit(main())
