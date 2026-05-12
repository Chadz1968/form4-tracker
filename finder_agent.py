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

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from edgar import set_identity, get_filings
import pandas as pd

from config import EDGAR_USER_AGENT

set_identity(EDGAR_USER_AGENT)

_FILING_TIMEOUT = 30  # seconds before a single filing.obj() call is abandoned
_MAX_WORKERS = int(os.getenv("FORM4_EDGAR_WORKERS", "6"))
_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_DIR, "cache")
_CACHE_VERSION = 1
_USE_TODAY_CACHE = os.getenv("FORM4_USE_TODAY_CACHE", "").lower() in ("1", "true", "yes")


def _cache_path(scan_date: str) -> str:
    return os.path.join(_CACHE_DIR, f"edgar_raw_{scan_date}.json")


def _cache_read_enabled(scan_date: str) -> bool:
    if _USE_TODAY_CACHE:
        return True
    try:
        return date.fromisoformat(scan_date) < date.today()
    except ValueError:
        return False


def _load_cached_trades(scan_date: str) -> list[dict] | None:
    if not _cache_read_enabled(scan_date):
        return None

    path = _cache_path(scan_date)
    if not os.path.exists(path):
        return None

    try:
        with open(path) as f:
            payload = json.load(f)
        if payload.get("version") != _CACHE_VERSION:
            return None

        trades = []
        for trade in payload.get("trades", []):
            trades.append({
                **trade,
                "p_trades": pd.DataFrame(trade.get("p_trades", [])),
            })
        print(f"[Finder] Loaded {len(trades)} raw P-trade filings from cache.")
        return trades
    except Exception as e:
        print(f"[Finder] Cache read failed for {scan_date}: {type(e).__name__}: {e}")
        return None


def _write_cached_trades(scan_date: str, trades: list[dict]) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    payload = {
        "version": _CACHE_VERSION,
        "scan_date": scan_date,
        "trades": [
            {
                **trade,
                "p_trades": trade["p_trades"].to_dict(orient="records"),
            }
            for trade in trades
        ],
    }
    path = _cache_path(scan_date)
    try:
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"[Finder] Cached {len(trades)} raw P-trade filings to {path}")
    except Exception as e:
        print(f"[Finder] Cache write failed for {scan_date}: {type(e).__name__}: {e}")


def _fetch_filing_obj(filing, timeout):
    """
    Calls filing.obj() on a daemon thread and returns (form4, elapsed) or
    (None, elapsed) on timeout.  Daemon threads are not joined, so a stalled
    EDGAR network call is abandoned immediately rather than blocking the caller.
    """
    result = [None]
    exc    = [None]
    done   = threading.Event()

    def _run():
        try:
            result[0] = filing.obj()
        except Exception as e:
            exc[0] = e
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t0 = time.monotonic()
    completed = done.wait(timeout=timeout)
    elapsed = time.monotonic() - t0

    if not completed:
        return None, elapsed
    if exc[0] is not None:
        raise exc[0]
    return result[0], elapsed


def _extract_purchase_trades(filing) -> tuple[dict | None, float, str | None]:
    accession = getattr(filing, "accession_no", None)
    form4, filing_elapsed = _fetch_filing_obj(filing, timeout=_FILING_TIMEOUT)
    if form4 is None:
        return None, filing_elapsed, accession

    market_trades = getattr(form4, "market_trades", None)
    if market_trades is None or len(market_trades) == 0:
        return None, filing_elapsed, accession

    p_trades = market_trades[market_trades["Code"] == "P"]
    if len(p_trades) == 0:
        return None, filing_elapsed, accession

    issuer       = getattr(form4, "issuer", None)
    company_name = getattr(issuer, "name",   "?") if issuer else "?"
    ticker       = getattr(issuer, "ticker", "?") if issuer else "?"

    return {
        "accession": accession,
        "ticker":    ticker,
        "company":   company_name,
        "insider":   getattr(form4, "insider_name",      "?"),
        "position":  getattr(form4, "position",          "") or "",
        "period":    getattr(form4, "reporting_period",  "?") or "",
        "p_trades":  p_trades,
    }, filing_elapsed, accession


def fetch_raw_trades(scan_date: str):
    """
    Generator. Fetches all Form 4 filings for scan_date and yields one
    dict per filing that contains at least one P-coded (open-market
    purchase) transaction.
    """
    cached = _load_cached_trades(scan_date)
    if cached is not None:
        yield from cached
        return

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
    print(f"[Finder] Index loaded — scanning filings with {_MAX_WORKERS} workers...")

    unique_filings = []
    seen = set()
    for filing in filings:
        accession = getattr(filing, "accession_no", None)
        if accession in seen:
            continue
        seen.add(accession)
        unique_filings.append(filing)

    checked    = 0
    found      = 0
    raw_trades = []
    loop_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = [executor.submit(_extract_purchase_trades, filing) for filing in unique_filings]
        for future in as_completed(futures):
            checked += 1
            try:
                trade, filing_elapsed, accession = future.result()
            except Exception as e:
                print(f"[Finder] Skipped filing: {type(e).__name__}: {e}")
                continue

            if checked % 50 == 0:
                elapsed = time.monotonic() - loop_start
                print(f"[Finder] Checked {checked}/{len(unique_filings)} filings, {found} with P-trades so far... ({elapsed:.0f}s)")

            if trade is None:
                if filing_elapsed >= _FILING_TIMEOUT:
                    print(f"[Finder] Timeout ({filing_elapsed:.0f}s) on filing {accession} — skipping")
                continue
            if filing_elapsed > 10:
                print(f"[Finder] Slow filing {accession} took {filing_elapsed:.1f}s")

            found += 1
            raw_trades.append(trade)

    total_elapsed = time.monotonic() - loop_start
    print(f"[Finder] Done — checked {checked} filings, {found} had P-trades. ({total_elapsed:.0f}s total)")
    _write_cached_trades(scan_date, raw_trades)
    yield from raw_trades
