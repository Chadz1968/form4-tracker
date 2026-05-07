"""
filter_agent.py

Receives raw trade records from finder_agent and applies the agreed
filtering criteria. Qualifying purchases are grouped by ticker for
cluster detection, then printed.

Filters applied (in order):
    1. Genuine corporate insider (role keyword match, not an institution)
    2. Valid exchange-listed ticker (1-5 uppercase letters, no numbers)
    3. Not a fund / partnership / REIT
    4. Filing period is recent (within MAX_FILING_AGE_DAYS of scan date)
    5. Total purchase value >= MIN_PURCHASE_VALUE
    6. Average purchase price >= MIN_STOCK_PRICE
"""

import re
import argparse
import datetime as dt
from datetime import datetime

from finder_agent import fetch_raw_trades

# ── Thresholds ────────────────────────────────────────────────
MIN_PURCHASE_VALUE  = 50_000
MIN_STOCK_PRICE     = 2.00
MAX_FILING_AGE_DAYS = 5

INSIDER_ROLES = [
    "ceo", "cfo", "president", "chairman", "director",
    "officer", "vp", "vice president", "chief", "evp", "svp",
]

FUND_KEYWORDS = [
    "fund", "trust", "partners", "lp ", " lp", "llc",
    "private", "interval", "reit",
]
# ─────────────────────────────────────────────────────────────


def is_valid_ticker(ticker: str) -> bool:
    if not ticker or ticker.upper() in ("NONE", "N/A", "?", ""):
        return False
    return bool(re.match(r'^[A-Z]{1,5}$', ticker.upper()))


def is_stale(period_str: str, scan_date: str) -> bool:
    try:
        filing_date = datetime.strptime(period_str[:10], "%Y-%m-%d").date()
        scan        = datetime.strptime(scan_date,       "%Y-%m-%d").date()
        return (scan - filing_date).days > MAX_FILING_AGE_DAYS
    except Exception:
        return True


def is_fund_like(company_name: str) -> bool:
    name_lower = company_name.lower()
    return any(kw in name_lower for kw in FUND_KEYWORDS)


def _value_and_avg(p_trades) -> tuple[float, float]:
    total_value  = 0.0
    total_shares = 0.0
    for _, row in p_trades.iterrows():
        try:
            total_value  += float(row["Shares"]) * float(row["Price"])
            total_shares += float(row["Shares"])
        except (TypeError, ValueError):
            pass
    avg = total_value / total_shares if total_shares else 0.0
    return total_value, avg


def filter_trades(raw_trades, scan_date: str):
    """
    Generator. Receives raw trade dicts from finder_agent and yields
    qualifying purchase dicts that pass all six filters.
    """
    for trade in raw_trades:
        position_lower = trade["position"].lower()

        # Filter 1: genuine corporate insider
        if not any(r in position_lower for r in INSIDER_ROLES):
            continue
        if "/" in trade["insider"]:          # institution masquerading as person
            continue

        # Filter 2: valid ticker
        if not is_valid_ticker(trade["ticker"]):
            continue

        # Filter 3: not a fund/partnership
        if is_fund_like(trade["company"]):
            continue

        # Filter 4: recent filing
        if is_stale(str(trade["period"]), scan_date):
            continue

        # Filter 5 & 6: dollar size and stock price
        p_trades              = trade["p_trades"]
        total_value, avg_price = _value_and_avg(p_trades)

        if total_value < MIN_PURCHASE_VALUE:
            continue
        if avg_price < MIN_STOCK_PRICE:
            continue

        yield {
            "ticker":    trade["ticker"],
            "company":   trade["company"],
            "insider":   trade["insider"],
            "position":  trade["position"],
            "period":    trade["period"],
            "value":     total_value,
            "avg_price": avg_price,
            "trades":    p_trades,
        }


def get_candidates(scan_date: str) -> dict[str, list[dict]]:
    """
    Run the full finder → filter pipeline and return purchases bucketed by
    ticker. Each value is a list of purchase dicts; length >= 2 means a
    cluster buy. This is the handoff point for llm_filter_agent.
    """
    raw_trades = fetch_raw_trades(scan_date)
    qualifying = filter_trades(raw_trades, scan_date)
    ticker_purchases: dict[str, list[dict]] = {}
    for purchase in qualifying:
        ticker_purchases.setdefault(purchase["ticker"], []).append(purchase)
    return ticker_purchases


def run(scan_date: str) -> None:
    print("=" * 60)
    print(f"Fetching Form 4 filings for {scan_date}...")
    print("=" * 60)
    print(f"Scanning for open-market purchases over "
          f"${MIN_PURCHASE_VALUE:,}...\n")

    ticker_purchases = get_candidates(scan_date)
    clusters    = [(t, ps) for t, ps in ticker_purchases.items() if len(ps) >= 2]
    single_buys = [(t, ps[0]) for t, ps in ticker_purchases.items() if len(ps) == 1]

    print(f"\n{'='*60}")
    print(f"RESULTS — {scan_date}")
    print(f"{'='*60}\n")

    if clusters:
        print(f"CLUSTER BUYS ({len(clusters)} companies)\n")
        for ticker, purchases in clusters:
            total_cluster_value = sum(p["value"] for p in purchases)
            print(f"  {purchases[0]['company']} ({ticker})")
            print(f"  Insiders: {len(purchases)} | "
                  f"Cluster value: ${total_cluster_value:,.0f}")
            for p in purchases:
                print(f"    -> {p['insider']} ({p['position']}): "
                      f"${p['value']:,.0f} @ avg ${p['avg_price']:.2f}")
            print()

    if single_buys:
        print(f"SINGLE BUYS ({len(single_buys)} companies)\n")
        for ticker, p in single_buys:
            print(f"  {p['company']} ({ticker})")
            print(f"  {p['insider']} -- {p['position']}")
            print(f"  ${p['value']:,.0f} @ avg ${p['avg_price']:.2f}")
            print()

    total_found = len(clusters) + len(single_buys)
    print(f"{'='*60}")
    print(f"Found {total_found} qualifying companies | "
          f"Clusters: {len(clusters)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Filter Form 4 filings for qualifying insider purchases"
    )
    parser.add_argument(
        "--date",
        default=dt.date.today().isoformat(),
        help="Scan date in YYYY-MM-DD format",
    )
    args = parser.parse_args()
    run(args.date)
