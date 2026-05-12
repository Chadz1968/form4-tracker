"""
market_scanner.py

Intraday setup scanner for the day trading manager coach.

Polls Alpaca market data every minute during market hours, detects the five
approved playbook setups, and pushes matching signals to signal_inbox.json
via trade_manager_journal.ingest_signal().

Run:
  python market_scanner.py

Each symbol fires at most one signal per setup per trading day, so the inbox
won't flood if a condition stays true across multiple bars.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import trade_manager_journal as journal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scanner")

NY = ZoneInfo("America/New_York")
_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(_DIR, "watchlist.json")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_watchlist() -> dict:
    if not os.path.exists(WATCHLIST_FILE):
        return {
            "symbols": ["NVDA", "AMD", "META", "TSLA", "AAPL"],
            "scan_interval_seconds": 60,
            "opening_range_minutes": 15,
            "min_relative_volume": 1.5,
            "vwap_pullback_tolerance_pct": 0.003,
        }
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Market hours
# ---------------------------------------------------------------------------

def is_market_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(NY)
    if now.weekday() >= 5:
        return False
    open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_dt <= now < close_dt


def next_open_message(now: datetime | None = None) -> str:
    now = now or datetime.now(NY)
    days_ahead = (7 - now.weekday()) % 7 if now.weekday() >= 5 else 0
    if now.weekday() < 5 and now.hour >= 16:
        days_ahead = 1 if now.weekday() < 4 else 3
    next_open = (now + timedelta(days=days_ahead)).replace(
        hour=9, minute=30, second=0, microsecond=0
    )
    return f"Market closed. Next open ~{next_open.strftime('%a %b %d %H:%M ET')}"


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------

def vwap(bars: list) -> float | None:
    total_tpv = sum((b.high + b.low + b.close) / 3.0 * b.volume for b in bars)
    total_vol = sum(b.volume for b in bars)
    return total_tpv / total_vol if total_vol > 0 else None


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class MarketScanner:
    def __init__(self, api_key: str, secret_key: str, config: dict) -> None:
        from alpaca.data.historical import StockHistoricalDataClient
        self._data = StockHistoricalDataClient(api_key, secret_key)
        self._config = config
        self._symbols: list[str] = config["symbols"]
        self._interval: int = int(config.get("scan_interval_seconds", 60))
        self._or_minutes: int = int(config.get("opening_range_minutes", 15))
        self._min_rvol: float = float(config.get("min_relative_volume", 1.5))
        self._vwap_tol: float = float(config.get("vwap_pullback_tolerance_pct", 0.003))

        # (symbol, setup) -> date last signaled — prevents inbox flooding
        self._signaled: dict[tuple[str, str], date] = {}
        # symbol -> (cache_date, avg_daily_volume)
        self._vol_cache: dict[str, tuple[date, float]] = {}

    def run(self) -> None:
        log.info("Scanner started. Watching: %s", ", ".join(self._symbols))
        log.info("Scan interval: %ds | OR window: %dm | Min RVOL: %.1fx",
                 self._interval, self._or_minutes, self._min_rvol)
        while True:
            now = datetime.now(NY)
            if is_market_hours(now):
                self._scan_all()
            else:
                log.info(next_open_message(now))
            time.sleep(self._interval)

    def _scan_all(self) -> None:
        for symbol in self._symbols:
            try:
                self._scan_symbol(symbol)
            except Exception as exc:
                log.warning("[%s] scan error: %s", symbol, exc)

    def _scan_symbol(self, symbol: str) -> None:
        bars = self._today_bars(symbol)
        if len(bars) < 3:
            return

        now_et = datetime.now(NY)
        or_cutoff = now_et.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(
            minutes=self._or_minutes
        )
        or_complete = now_et >= or_cutoff
        or_bars = [b for b in bars if b.timestamp < or_cutoff.astimezone(timezone.utc)]

        current_vwap = vwap(bars)
        if current_vwap is None:
            return

        rvol = self._relative_volume(symbol, bars, now_et)
        cur = bars[-1]
        prev = bars[-2]

        self._check_orb(symbol, bars, or_bars, or_complete, cur, prev, current_vwap, rvol)
        self._check_vwap_reclaim(symbol, cur, prev, current_vwap, rvol)
        self._check_vwap_pullback(symbol, bars, cur, prev, current_vwap, rvol)
        self._check_news_momentum(symbol, bars, cur, current_vwap, rvol)
        self._check_failed_breakdown(symbol, bars, cur, prev, current_vwap, rvol)

    # ------------------------------------------------------------------
    # Setup detectors
    # ------------------------------------------------------------------

    def _check_orb(self, symbol, bars, or_bars, or_complete, cur, prev, current_vwap, rvol):
        if not or_complete or len(or_bars) < 2:
            return
        or_high = max(b.high for b in or_bars)
        # previous bar was at or below OR high; current bar closes above it
        if prev.close <= or_high < cur.close:
            self._emit(symbol, "opening_range_breakout", "breaks_opening_range",
                       cur, current_vwap, rvol,
                       f"Broke above OR high {or_high:.2f}")

    def _check_vwap_reclaim(self, symbol, cur, prev, current_vwap, rvol):
        if prev.close < current_vwap and cur.close > current_vwap and cur.close > prev.close:
            self._emit(symbol, "vwap_reclaim", "close_above_vwap",
                       cur, current_vwap, rvol, "Reclaimed VWAP on up-close")

    def _check_vwap_pullback(self, symbol, bars, cur, prev, current_vwap, rvol):
        if len(bars) < 10:
            return
        trend_up = bars[-5].close > bars[-10].close
        near_vwap = abs(cur.low - current_vwap) / current_vwap < self._vwap_tol
        holding = cur.close > current_vwap and cur.close > prev.close
        if trend_up and near_vwap and holding:
            self._emit(symbol, "vwap_pullback", "vwap_hold",
                       cur, current_vwap, rvol, "Pulled back to VWAP, holding above")

    def _check_news_momentum(self, symbol, bars, cur, current_vwap, rvol):
        if rvol is None or rvol < self._min_rvol:
            return
        if len(bars) < 5:
            return
        strong_trend = cur.close > bars[-5].close and cur.close > current_vwap
        if strong_trend:
            self._emit(symbol, "news_momentum", "trend_confirmation",
                       cur, current_vwap, rvol,
                       f"High RVOL ({rvol:.1f}x) with uptrend above VWAP — confirm catalyst manually")

    def _check_failed_breakdown(self, symbol, bars, cur, prev, current_vwap, rvol):
        if len(bars) < 7:
            return
        lookback = bars[-7:-1]
        support = min(b.low for b in lookback)
        broke_below = prev.low < support
        reclaimed = cur.close > support and cur.volume > prev.volume
        if broke_below and reclaimed:
            self._emit(symbol, "failed_breakdown_reversal", "reclaim_confirmation",
                       cur, current_vwap, rvol,
                       f"Broke below {support:.2f} then reclaimed on volume")

    # ------------------------------------------------------------------
    # Signal emission with per-day dedup
    # ------------------------------------------------------------------

    def _emit(self, symbol: str, setup: str, trigger: str, bar,
              current_vwap: float, rvol: float | None, detail: str) -> None:
        key = (symbol, setup)
        today = date.today()
        if self._signaled.get(key) == today:
            return
        self._signaled[key] = today

        rvol_str = f"rvol={rvol:.1f}x" if rvol is not None else "rvol=n/a"
        notes = f"{detail}. VWAP={current_vwap:.2f}, {rvol_str}"

        signal = journal.ingest_signal({
            "source": "scanner",
            "symbol": symbol,
            "timeframe": "1m",
            "setup": setup,
            "side": "long",
            "price": float(bar.close),
            "trigger": trigger,
            "notes": notes,
            "bar_time": bar.timestamp.isoformat(),
        })
        log.info("SIGNAL  %-6s  %-30s  $%.2f  %s  [id=%s]",
                 symbol, setup, bar.close, rvol_str, signal["id"][:8])

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _today_bars(self, symbol: str) -> list:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        now_et = datetime.now(NY)
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=market_open.astimezone(timezone.utc),
            end=datetime.now(timezone.utc),
        )
        result = self._data.get_stock_bars(req)
        return list(result.get(symbol, []))

    def _relative_volume(self, symbol: str, today_bars: list,
                         now_et: datetime) -> float | None:
        avg = self._avg_daily_volume(symbol, now_et)
        if avg is None or avg == 0:
            return None
        minutes_elapsed = (now_et.hour * 60 + now_et.minute) - (9 * 60 + 30)
        if minutes_elapsed <= 0:
            return None
        today_vol = sum(b.volume for b in today_bars)
        extrapolated = today_vol * (390.0 / minutes_elapsed)
        return extrapolated / avg

    def _avg_daily_volume(self, symbol: str, now_et: datetime) -> float | None:
        today = now_et.date()
        cached = self._vol_cache.get(symbol)
        if cached and cached[0] == today:
            return cached[1]

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=(now_et - timedelta(days=35)).astimezone(timezone.utc),
                end=now_et.astimezone(timezone.utc),
            )
            daily = list(self._data.get_stock_bars(req).get(symbol, []))
            # exclude today (partial) — use last 20 complete days
            complete = [b for b in daily if b.timestamp.date() < today][-20:]
            if len(complete) < 5:
                return None
            avg = sum(b.volume for b in complete) / len(complete)
            self._vol_cache[symbol] = (today, avg)
            return avg
        except Exception as exc:
            log.debug("[%s] volume cache miss: %s", symbol, exc)
            return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env")

    config = load_watchlist()
    scanner = MarketScanner(api_key, secret_key, config)
    scanner.run()


if __name__ == "__main__":
    main()
