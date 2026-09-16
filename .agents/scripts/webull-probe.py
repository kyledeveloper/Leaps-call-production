#!/usr/bin/env python3
"""
Webull OpenAPI Live Probe Tool
Diagnoses authentication, accounts, option contracts, and market data subscriptions.
"""
import json
import os
import sys
from datetime import datetime, timezone

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.leaps_scanner.data.webull import (
    WebullClient,
    LogSanitizer,
    parse_webull_contracts_response
)

def load_env_file():
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.env"))
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

def main():
    load_env_file()
    print("=" * 65)
    print("🔍 Webull OpenAPI Live Connectivity & Market Data Probe")
    print("=" * 65)

    app_key = os.environ.get("WEBULL_APP_KEY", "")
    app_secret = os.environ.get("WEBULL_APP_SECRET", "")

    if not app_key or not app_secret:
        print("❌ Error: WEBULL_APP_KEY or WEBULL_APP_SECRET missing in .env")
        sys.exit(1)

    masked_key = app_key[:6] + "..." + app_key[-4:] if len(app_key) > 10 else "***"
    print(f"• App Key: {masked_key}")
    print(f"• Region: {os.environ.get('WEBULL_REGION_ID', 'us')}")

    # Initialize client in live mode
    client = WebullClient(offline_mode=False)

    print("\n[Step 1] Checking 2FA Access Token...")
    try:
        token = client.auth.get_token()
        masked_tok = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
        print(f"  ✅ Access Token Active: {masked_tok}")
    except Exception as e:
        print(f"  ❌ Token Retrieval Failed: {e}")
        sys.exit(1)

    print("\n[Step 2] Querying Account List...")
    accounts = client.get_accounts()
    print(f"  ✅ Found {len(accounts)} accounts linked to this Webull profile:")
    primary_acc = None
    for acc in accounts:
        acc_id = acc.get("account_id", "")
        masked_id = acc_id[:6] + "..." + acc_id[-4:]
        acc_label = acc.get("account_label", "Unknown")
        acc_type = acc.get("account_type", "")
        print(f"     - [{acc_type}] {acc_label}: ID={masked_id}")
        if "MARGIN" in acc.get("account_class", ""):
            primary_acc = acc_id

    if primary_acc:
        print(f"\n[Step 3] Fetching Assets for Margin Account ({primary_acc[:6]}...):")
        balance = client.get_balances(primary_acc)
        net_val = balance.get("total_net_liquidation_value", "0.00")
        cash = balance.get("total_cash_balance", "0.00")
        print(f"     - Net Liquidation Value: ${net_val}")
        print(f"     - Cash Balance: ${cash}")

    print("\n[Step 4] Querying Real LEAPS Contracts (AAPL)...")
    aapl_contracts = client.query_options_contracts("AAPL")
    leaps_candidates = parse_webull_contracts_response({"data": aapl_contracts})
    print(f"  ✅ Total AAPL Contracts retrieved: {len(aapl_contracts)}")
    print(f"  ✅ Filtered LEAPS Contracts (DTE >= 250d): {len(leaps_candidates)}")
    if leaps_candidates:
        print("     Sample LEAPS Contracts:")
        for c in leaps_candidates[:5]:
            print(f"       • {c.symbol} | Strike: ${c.strike:.1f} | DTE: {c.dte:.0f}d | Quality: {c.data_quality}")

    print("\n[Step 5] Diagnosing Market Data Quotes Permission...")
    status, snap_resp = client._http_request(
        uri="/market-data/options/snapshots/list",
        queries={"symbols": leaps_candidates[0].symbol if leaps_candidates else "AAPL261218C00240000", "category": "US_OPTION"}
    )
    if status == 200:
        print("  🎉 Real-time Option Quotes Permission: ACTIVE")
    elif status == 403:
        err_code = snap_resp.get("error_code") if isinstance(snap_resp, dict) else ""
        print(f"  ⚠️ Real-time Option Quotes: NOT SUBSCRIBED (Code: {err_code})")
        print("     ℹ️ To activate real-time quotes, visit Webull App / Developer Console:")
        print("        https://developer.webull.com/apis/docs/market-data-api/subscribe-quotes")
        print("     ℹ️ Note: The scanner automatically degrades to METADATA_ONLY mode safely.")

    print("\n" + "=" * 65)
    print("🎯 Webull OpenAPI Connectivity Verification Complete!")
    print("=" * 65)

if __name__ == "__main__":
    main()
