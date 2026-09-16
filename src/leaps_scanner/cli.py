"""
Command Line Interface for LEAPS Call Quant Scanner.
Outputs formatted financial strategy boards to terminal and supports launching web dashboard.
"""
import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

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
    parser.add_argument("--universe", type=str, default=None, choices=["sp100", "nasdaq100", "djia", "etfs", "adrs", "all"], help="Predefined universe: sp100, nasdaq100, djia, etfs, adrs, all")
    parser.add_argument("--sync-universe", action="store_true", help="Check remote sources and sync index constituents")
    parser.add_argument("--alpha", type=float, default=0.5, help="Execution slippage alpha in [0.0, 1.0]")
    parser.add_argument("--strategy", type=str, default="all", choices=["all", "deep_itm", "vol_discount", "oversold", "unusual_flow"], help="Strategy filter")
    parser.add_argument("--serve", action="store_true", help="Launch interactive Web Dashboard HTTP server")
    parser.add_argument("--port", type=int, default=8000, help="Web Dashboard port")
    parser.add_argument("--offline", action="store_true", default=True, help="Force offline sandbox mode")

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

    # 4. Unusual Flow Board
    if args.strategy in ("all", "unusual_flow"):
        items = boards.get("unusual_flow", [])
        headers = ["Symbol", "Underlying", "Strike", "DTE", "P_exec", "Vol / OI", "Vol/OI Ratio", "Dollar Vol", "Status"]
        rows = [
            [
                it["symbol"],
                it["underlying"],
                f"${it['strike']:.1f}",
                f"{int(it['dte'])}d",
                f"${it['p_exec']:.2f}",
                f"{it['volume']} / {it['open_interest']}",
                f"{it.get('vol_oi_ratio', 0.0):.2f}x",
                f"${it.get('dollar_volume', 0.0):,.0f}",
                it["status"]
            ]
            for it in items
        ]
        print(format_ascii_table("Strategy 4: Unusual Far-Dated Options Flow", headers, rows))
        print("Note: Far-dated flow includes rollovers, tax-loss harvesting, and structured hedging;")
        print("      signals direction with lower certainty than short-dated UOA.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
