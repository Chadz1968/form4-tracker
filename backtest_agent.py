"""
backtest_agent.py

Replays historical Form 4 scan dates, collects qualifying signals, then
computes 1-week / 4-week / 12-week forward returns versus SPY.

Two-phase design so long runs are resumable:
  Phase 1 — collect_signals()  iterates trading days, runs the finder+filter
             pipeline for each, and appends results to backtest_signals.json.
  Phase 2 — build_results()    loads the checkpoint, bulk-downloads prices via
             yfinance, computes alpha vs SPY, and writes backtest_results.csv.

Usage:
    python backtest_agent.py --start 2022-01-01 --end 2024-12-31
    python backtest_agent.py --start 2022-01-01 --end 2024-12-31 --resume
    python backtest_agent.py --returns-only --end 2024-12-31
    python backtest_agent.py --start 2024-01-01 --end 2024-01-31 --max-days 10
"""

import argparse
import datetime as dt
import json
import os
import time

import pandas as pd
import yfinance as yf

import config
from filter_agent import get_candidates
from keep_awake import keep_system_awake

_DIR         = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(_DIR, "backtest_signals.json")
RESULTS_FILE = os.path.join(_DIR, "backtest_results.csv")

HOLD_DAYS        = {"1w": 5, "4w": 20, "12w": 60}
PRICE_BUFFER     = 100   # extra calendar days beyond end_date for 12w returns
PRICE_BATCH_SIZE = 100   # tickers per yfinance download call


# ── Date helpers ──────────────────────────────────────────────

def get_trading_days(start: str, end: str) -> list[dt.date]:
    """Return Mon–Fri dates between start and end inclusive (approximate trading days)."""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days, cur = [], s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur)
        cur += dt.timedelta(days=1)
    return days


# ── Checkpoint helpers ────────────────────────────────────────

def _load_done_dates() -> set[str]:
    if not os.path.exists(SIGNALS_FILE):
        return set()
    with open(SIGNALS_FILE) as f:
        return {r["scan_date"] for r in json.load(f)}


def _append_records(records: list[dict]) -> None:
    existing = []
    if os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE) as f:
            existing = json.load(f)
    existing.extend(records)
    with open(SIGNALS_FILE, "w") as f:
        json.dump(existing, f, indent=2, default=str)


# ── Signal collection ─────────────────────────────────────────

def _role_tier(position: str) -> str:
    p = position.lower()
    if any(k in p for k in ["ceo", "cfo", "chairman", "president"]):
        return "tier1"
    if any(k in p for k in ["evp", "svp", "chief", "vice president", "vp"]):
        return "tier2"
    return "tier3"


def _scan_date(scan_date: str) -> list[dict]:
    try:
        ticker_purchases = get_candidates(scan_date)
    except Exception as e:
        print(f"[Backtest] {scan_date} — scan failed: {type(e).__name__}: {e}")
        return []

    signals = []
    for ticker, purchases in ticker_purchases.items():
        cluster_size = len(purchases)
        for p in purchases:
            signals.append({
                "scan_date":    scan_date,
                "ticker":       ticker,
                "company":      p["company"],
                "insider":      p["insider"],
                "position":     p["position"],
                "value":        round(p["value"], 2),
                "avg_price":    round(p["avg_price"], 2),
                "cluster_size": cluster_size,
                "is_cluster":   cluster_size >= 2,
                "role_tier":    _role_tier(p["position"]),
            })
    return signals


def collect_signals(
    start: str,
    end: str,
    resume: bool = False,
    delay: float = 1.0,
    max_days: int | None = None,
) -> None:
    """
    Iterate trading days between start and end, run the filter pipeline for
    each, and save signals incrementally to backtest_signals.json.

    Args:
        delay:    Seconds to sleep between scan dates (EDGAR rate courtesy).
        max_days: Cap the number of dates scanned — useful for smoke-tests.
        resume:   Skip dates already present in the checkpoint file.
    """
    with keep_system_awake("Form 4 backtest collection"):
        days = get_trading_days(start, end)
        if max_days:
            days = days[:max_days]

        done = _load_done_dates() if resume else set()
        if not resume and os.path.exists(SIGNALS_FILE):
            os.remove(SIGNALS_FILE)

        pending = [d for d in days if d.isoformat() not in done]
        print(f"[Backtest] {len(days)} trading days | {len(done)} cached | {len(pending)} to scan")

        for i, day in enumerate(pending, 1):
            date_str = day.isoformat()
            print(f"\n[Backtest] [{i}/{len(pending)}] Scanning {date_str}...")
            signals = _scan_date(date_str)
            # Always write a record for this date so resume skips it correctly
            _append_records(signals if signals else [{"scan_date": date_str, "_empty": True}])
            print(f"[Backtest] {date_str} — {len(signals)} signals saved")
            time.sleep(delay)

        print(f"\n[Backtest] Collection complete. Checkpoint: {SIGNALS_FILE}")


# ── Return calculation ────────────────────────────────────────

def _download_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    end_padded = (dt.date.fromisoformat(end) + dt.timedelta(days=PRICE_BUFFER)).isoformat()
    symbols    = sorted(set(tickers + ["SPY"]))
    frames     = []

    for i in range(0, len(symbols), PRICE_BATCH_SIZE):
        batch = symbols[i : i + PRICE_BATCH_SIZE]
        print(f"[Backtest] Downloading prices: tickers {i+1}–{min(i+PRICE_BATCH_SIZE, len(symbols))} of {len(symbols)}...")
        raw = yf.download(batch, start=start, end=end_padded, auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            frames.append(raw["Close"])
        else:
            # Single-ticker download returns a flat DataFrame
            frames.append(raw[["Close"]].rename(columns={"Close": batch[0]}))

    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def _fwd_return(prices: pd.DataFrame, ticker: str, scan_date: str, hold_days: int) -> float | None:
    """
    Percentage return from the first close on-or-after scan_date to hold_days
    trading sessions later. Returns None if price data is unavailable.
    """
    try:
        col = prices[ticker].dropna()
        idx = col.index.searchsorted(pd.Timestamp(scan_date))
        if idx >= len(col) - hold_days:
            return None
        entry = float(col.iloc[idx])
        exit_ = float(col.iloc[idx + hold_days])
        return round((exit_ - entry) / entry * 100, 4) if entry else None
    except Exception:
        return None


def build_results(end: str) -> pd.DataFrame:
    """
    Load the checkpoint, compute forward returns for every signal, write
    backtest_results.csv, and return the results DataFrame.
    """
    with keep_system_awake("Form 4 backtest return build"):
        if not os.path.exists(SIGNALS_FILE):
            print(f"[Backtest] Checkpoint not found: {SIGNALS_FILE}. Run collect_signals first.")
            return pd.DataFrame()

        with open(SIGNALS_FILE) as f:
            raw = json.load(f)

        signals = [s for s in raw if not s.get("_empty")]
        if not signals:
            print("[Backtest] No signals in checkpoint.")
            return pd.DataFrame()

        df    = pd.DataFrame(signals)
        start = df["scan_date"].min()
        print(f"[Backtest] {len(df):,} signals across {df['scan_date'].nunique()} dates")

        prices = _download_prices(df["ticker"].unique().tolist(), start, end)

        print("[Backtest] Computing forward returns...")
        for label, days in HOLD_DAYS.items():
            df[f"ret_{label}"]     = df.apply(
                lambda r: _fwd_return(prices, r["ticker"], r["scan_date"], days), axis=1
            )
            df[f"spy_ret_{label}"] = df.apply(
                lambda r: _fwd_return(prices, "SPY", r["scan_date"], days), axis=1
            )
            df[f"alpha_{label}"]   = df[f"ret_{label}"] - df[f"spy_ret_{label}"]

        df.to_csv(RESULTS_FILE, index=False)
        print(f"[Backtest] Results written to {RESULTS_FILE} ({len(df):,} rows)")
        return df


# ── Convenience wrapper ───────────────────────────────────────

def run_backtest(
    start: str,
    end: str,
    resume: bool = False,
    delay: float = 1.0,
    max_days: int | None = None,
) -> pd.DataFrame:
    collect_signals(start, end, resume=resume, delay=delay, max_days=max_days)
    return build_results(end)


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Form 4 backtest: collect historical signals and compute forward returns"
    )
    parser.add_argument("--start",        default="2022-01-01",
                        help="Start date YYYY-MM-DD (default 2022-01-01)")
    parser.add_argument("--end",          default="2024-12-31",
                        help="End date YYYY-MM-DD (default 2024-12-31)")
    parser.add_argument("--resume",       action="store_true",
                        help="Resume from existing checkpoint (skip already-scanned dates)")
    parser.add_argument("--returns-only", action="store_true",
                        help="Skip scanning; compute returns from existing checkpoint")
    parser.add_argument("--delay",        type=float, default=1.0,
                        help="Seconds between scan dates, default 1.0")
    parser.add_argument("--max-days",     type=int, default=None,
                        help="Limit to first N trading days (smoke-test mode)")
    args = parser.parse_args()

    config.validate()

    if args.returns_only:
        build_results(args.end)
    else:
        run_backtest(
            args.start, args.end,
            resume=args.resume,
            delay=args.delay,
            max_days=args.max_days,
        )
