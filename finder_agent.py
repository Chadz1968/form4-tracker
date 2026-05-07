"""
finder_agent.py

Queries EDGAR for Form 4 filings on a given date and yields raw
purchase-trade records for each filing that contains at least one
open-market buy (transaction code "P").

Yields dicts with keys:
    accession  – filing accession number (str)
    ticker     – issuer ticker symbol (str)
    company    – issuer company name (str)
    insider    – reporting person name (str)
    position   – reporting person title/role (str)
    period     – reporting period date string (str)
    p_trades   – DataFrame of Code=="P" rows from market_trades
"""

from edgar import set_identity, get_filings

from config import EDGAR_USER_AGENT

set_identity(EDGAR_USER_AGENT)


def fetch_raw_trades(scan_date: str):
    """
    Generator. Fetches all Form 4 filings for scan_date and yields one
    dict per filing that contains at least one P-coded (open-market
    purchase) transaction.
    """
    year    = int(scan_date[:4])
    month   = int(scan_date[5:7])
    quarter = (month - 1) // 3 + 1

    print(f"[Finder] Downloading filing index for {scan_date}...")
    filings = get_filings(
        year=year,
        quarter=quarter,
        form="4",
        filing_date=scan_date,
    )
    print(f"[Finder] Index loaded — scanning filings...")

    seen    = set()
    checked = 0
    found   = 0

    for filing in filings:
        try:
            accession = getattr(filing, "accession_no", None)
            if accession in seen:
                continue
            seen.add(accession)
            checked += 1

            if checked % 50 == 0:
                print(f"[Finder] Checked {checked} filings, {found} with P-trades so far...")

            form4 = filing.obj()

            market_trades = getattr(form4, "market_trades", None)
            if market_trades is None or len(market_trades) == 0:
                continue

            p_trades = market_trades[market_trades["Code"] == "P"]
            if len(p_trades) == 0:
                continue

            issuer       = getattr(form4, "issuer", None)
            company_name = getattr(issuer, "name",   "?") if issuer else "?"
            ticker       = getattr(issuer, "ticker", "?") if issuer else "?"

            found += 1
            yield {
                "accession": accession,
                "ticker":    ticker,
                "company":   company_name,
                "insider":   getattr(form4, "insider_name",      "?"),
                "position":  getattr(form4, "position",          "") or "",
                "period":    getattr(form4, "reporting_period",  "?") or "",
                "p_trades":  p_trades,
            }

        except Exception as e:
            print(f"[Finder] Skipped filing (accession={accession}): {type(e).__name__}: {e}")
            continue

    print(f"[Finder] Done — checked {checked} filings, {found} had P-trades.")
