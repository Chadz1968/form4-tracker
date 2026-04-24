from edgar import set_identity, get_filings
from datetime import datetime, date, timedelta
import re

set_identity("Mark Chadwick chadwick_mark@hotmail.com")

# ── Configuration ────────────────────────────────────────────
MIN_PURCHASE_VALUE  = 50_000
MIN_STOCK_PRICE     = 2.00      # filter penny stocks
MAX_FILING_AGE_DAYS = 5         # filter stale filings
SCAN_DATE           = "2025-04-28"
SCAN_YEAR           = 2025
SCAN_QUARTER        = 2

INSIDER_ROLES = [
    "ceo", "cfo", "president", "chairman", "director",
    "officer", "vp", "vice president", "chief", "evp", "svp"
]

# Patterns that indicate non-exchange-listed products
FUND_KEYWORDS = [
    "fund", "trust", "partners", "lp ", " lp", "llc",
    "private", "interval", "reit"
]
# ─────────────────────────────────────────────────────────────


def is_valid_ticker(ticker: str) -> bool:
    """
    Reject missing, placeholder, or clearly invalid tickers.
    Valid tickers are 1-5 uppercase letters, no numbers.
    """
    if not ticker or ticker.upper() in ("NONE", "N/A", "?", ""):
        return False
    return bool(re.match(r'^[A-Z]{1,5}$', ticker.upper()))


def is_stale(filing_date_str: str, max_age_days: int) -> bool:
    """
    Return True if the filing date is older than max_age_days
    before the scan date.
    """
    try:
        # Handle timezone suffix like '-05:00'
        clean = filing_date_str[:10]
        filing_date = datetime.strptime(clean, "%Y-%m-%d").date()
        scan_date   = datetime.strptime(SCAN_DATE, "%Y-%m-%d").date()
        return (scan_date - filing_date).days > max_age_days
    except Exception:
        return True   # if we can't parse it, treat as stale


def is_fund_like(company_name: str) -> bool:
    """
    Return True if the company name suggests a non-traded
    fund or partnership — not a publicly listed equity.
    """
    name_lower = company_name.lower()
    return any(kw in name_lower for kw in FUND_KEYWORDS)


def get_avg_price(p_trades) -> float:
    """Calculate the average price across all purchase rows."""
    total_value  = 0.0
    total_shares = 0.0
    for _, row in p_trades.iterrows():
        try:
            total_value  += float(row["Shares"]) * float(row["Price"])
            total_shares += float(row["Shares"])
        except (TypeError, ValueError):
            pass
    if total_shares == 0:
        return 0.0
    return total_value / total_shares


print("=" * 60)
print(f"Fetching Form 4 filings for {SCAN_DATE}...")
print("=" * 60)

filings = get_filings(
    year=SCAN_YEAR,
    quarter=SCAN_QUARTER,
    form="4",
    filing_date=SCAN_DATE
)

print(f"Scanning for open-market purchases over "
      f"${MIN_PURCHASE_VALUE:,}...\n")

found   = 0
checked = 0
errors  = 0
seen    = set()

# Track per-ticker purchases for cluster detection
ticker_purchases: dict[str, list[dict]] = {}

for filing in filings:
    checked += 1

    try:
        accession = getattr(filing, "accession_no", None)
        if accession in seen:
            continue
        seen.add(accession)

        form4 = filing.obj()

        # ── Filter 1: must have P transactions ───────────────
        market_trades = getattr(form4, "market_trades", None)
        if market_trades is None or len(market_trades) == 0:
            continue
        p_trades = market_trades[market_trades["Code"] == "P"]
        if len(p_trades) == 0:
            continue

        # ── Filter 2: genuine corporate insider ──────────────
        insider  = getattr(form4, "insider_name", "?")
        position = getattr(form4, "position", "") or ""
        position_lower = position.lower()

        is_insider    = any(r in position_lower for r in INSIDER_ROLES)
        is_institution = "/" in insider
        if not is_insider or is_institution:
            continue

        # ── Extract company info ──────────────────────────────
        issuer       = getattr(form4, "issuer", None)
        company_name = getattr(issuer, "name",   "?") if issuer else "?"
        ticker       = getattr(issuer, "ticker", "?") if issuer else "?"
        period       = getattr(form4, "reporting_period", "?") or ""

        # ── Filter 3: valid exchange-listed ticker ────────────
        if not is_valid_ticker(ticker):
            continue

        # ── Filter 4: not a fund or partnership ──────────────
        if is_fund_like(company_name):
            continue

        # ── Filter 5: filing must be recent ──────────────────
        if is_stale(str(period), MAX_FILING_AGE_DAYS):
            continue

        # ── Calculate total purchase value & avg price ───────
        total_value = sum(
            float(r["Shares"]) * float(r["Price"])
            for _, r in p_trades.iterrows()
            if r["Price"] and r["Shares"]
        )
        avg_price = get_avg_price(p_trades)

        # ── Filter 6: minimum dollar size ────────────────────
        if total_value < MIN_PURCHASE_VALUE:
            continue

        # ── Filter 7: minimum stock price ────────────────────
        if avg_price < MIN_STOCK_PRICE:
            continue

        # ── Store for cluster detection ───────────────────────
        purchase = {
            "ticker":    ticker,
            "company":   company_name,
            "insider":   insider,
            "position":  position,
            "period":    period,
            "value":     total_value,
            "avg_price": avg_price,
            "trades":    p_trades,
        }

        if ticker not in ticker_purchases:
            ticker_purchases[ticker] = []
        ticker_purchases[ticker].append(purchase)

        found += 1

    except Exception as e:
        errors += 1
        continue


# ── Cluster detection & output ────────────────────────────────
print(f"\n{'='*60}")
print(f"RESULTS — {SCAN_DATE}")
print(f"{'='*60}\n")

clusters     = []
single_buys  = []

for ticker, purchases in ticker_purchases.items():
    if len(purchases) >= 2:
        clusters.append((ticker, purchases))
    else:
        single_buys.append((ticker, purchases[0]))

# Print clusters first — highest priority
if clusters:
    print(f"🔴 CLUSTER BUYS ({len(clusters)} companies)\n")
    for ticker, purchases in clusters:
        total_cluster_value = sum(p["value"] for p in purchases)
        print(f"  {purchases[0]['company']} ({ticker})")
        print(f"  Insiders: {len(purchases)} | "
              f"Cluster value: ${total_cluster_value:,.0f}")
        for p in purchases:
            print(f"    → {p['insider']} ({p['position']}): "
                  f"${p['value']:,.0f} @ avg ${p['avg_price']:.2f}")
        print()

# Print single buys second
if single_buys:
    print(f"⚪ SINGLE BUYS ({len(single_buys)} companies)\n")
    for ticker, p in single_buys:
        print(f"  {p['company']} ({ticker})")
        print(f"  {p['insider']} — {p['position']}")
        print(f"  ${p['value']:,.0f} @ avg ${p['avg_price']:.2f}")
        print()

print(f"{'='*60}")
print(f"Checked {checked} filings | "
      f"Found {found} qualifying | "
      f"Clusters: {len(clusters)} | "
      f"Errors: {errors}")