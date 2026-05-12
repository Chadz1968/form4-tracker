"""
ngrok_status.py

Print the current ngrok tunnel URL and ready-to-paste TradingView webhook configs.

Run any time after starting ngrok:
  python ngrok_status.py
"""

import json
import sys
import urllib.request

NGROK_API = "http://127.0.0.1:4040/api/tunnels"
WEBHOOK_PATH = "/webhook/tradingview"

SETUPS = [
    ("opening_range_breakout", "breaks_opening_range",    "ORB"),
    ("vwap_reclaim",           "close_above_vwap",        "VWAP Reclaim"),
    ("vwap_pullback",          "vwap_hold",               "VWAP Pullback"),
    ("news_momentum",          "trend_confirmation",       "News Momentum"),
    ("failed_breakdown_reversal", "reclaim_confirmation", "Failed Breakdown"),
]


def get_tunnel_url() -> str:
    try:
        with urllib.request.urlopen(NGROK_API, timeout=3) as resp:
            data = json.loads(resp.read())
    except OSError:
        print("ERROR: ngrok is not running (nothing on 127.0.0.1:4040).")
        print("Start it with:  ngrok http 8765")
        sys.exit(1)

    https = [t["public_url"] for t in data.get("tunnels", []) if t.get("proto") == "https"]
    if not https:
        print("ERROR: ngrok is running but no HTTPS tunnel found.")
        sys.exit(1)
    return https[0]


def alert_body(setup: str, trigger: str) -> str:
    slug = setup.replace("_", "")[:12]
    return (
        '{"symbol":"{{ticker}}",'
        '"timeframe":"{{interval}}",'
        f'"setup":"{setup}",'
        '"side":"long",'
        '"price":{{close}},'
        f'"trigger":"{trigger}",'
        '"notes":"{{exchange}}:{{ticker}} at {{time}}",'
        f'"alert_id":"{{{{ticker}}}}-{slug}-{{{{time}}}}"'
        "}"
    )


def main() -> None:
    url = get_tunnel_url()
    webhook = url + WEBHOOK_PATH

    print()
    print("=" * 60)
    print("  ngrok tunnel is LIVE")
    print("=" * 60)
    print(f"  Public URL : {url}")
    print(f"  Webhook    : {webhook}")
    print()
    print("  Paste the Webhook URL above into every TradingView alert.")
    print()
    print("-" * 60)
    print("  Alert message bodies (one per setup):")
    print("-" * 60)
    for setup, trigger, label in SETUPS:
        print(f"\n  [{label}]")
        print(f"  {alert_body(setup, trigger)}")
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
