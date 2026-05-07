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

import concurrent.futures
import time

from edgar import set_identity, get_filings

from config import EDGAR_USER_AGENT

set_identity(EDGAR_USER_AGENT)

_FILING_TIMEOUT = 30  # seconds before a single filing.obj() call is abandoned


def _parse_filing(filing):
    """Wraps filing.obj() so it can be run in a thread with a timeout."""
    return filing.obj()


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

    seen       = set()
    checked    = 0
    found      = 0
    loop_start = time.monotonic()

    for filing in filings:
        accession = getattr(filing, "accession_no", None)
        try:
            if accession in seen:
                continue
            seen.add(accession)
            checked += 1

            if checked % 50 == 0:
                elapsed = time.monotonic() - loop_start
                print(f"[Finder] Checked {checked} filings, {found} with P-trades so far... ({elapsed:.0f}s)")

            filing_start = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_parse_filing, filing)
                try:
                    form4 = future.result(timeout=_FILING_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    elapsed = time.monotonic() - filing_start
                    print(f"[Finder] Timeout ({elapsed:.0f}s) on filing {accession} — skipping")
                    continue

            filing_elapsed = time.monotonic() - filing_start
            if filing_elapsed > 10:
                print(f"[Finder] Slow filing {accession} took {filing_elapsed:.1f}s")

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

    total_elapsed = time.monotonic() - loop_start
    print(f"[Finder] Done — checked {checked} filings, {found} had P-trades. ({total_elapsed:.0f}s total)")
