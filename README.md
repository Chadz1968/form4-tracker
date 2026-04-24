# Insider Signals

A research project exploring whether SEC Form 4 insider-buy filings produce
tradeable edge for retail investors.

## Background

This project builds on an earlier gap-momentum trading system (archived at
[link to gap-fade repo]) which concluded that retail-accessible
price-based signals on S&P 500 names do not produce durable edge after costs.

The hypothesis here is different: SEC Form 4 filings — legally required
disclosures of insider transactions — may contain predictive information,
particularly for cluster buys, CEO/CFO buys, and non-routine purchases.
Academic literature (Seyhun 1986; Cohen/Malloy/Pomorski 2012) finds roughly
6% annualised alpha for opportunistic insider buys, which is both plausible
for retail and small enough to be credible.

## Research Questions

1. Does a naive "follow insider buys" strategy produce edge after costs on
   S&P 500 names in 2022-2024 backtests?
2. Do cluster buys outperform single-insider buys?
3. Does an LLM-based qualitative filter (role, context, recent news) add
   value over mechanical filtering?
4. Are holding periods of 1 week, 4 weeks, or 12 weeks optimal?

## Status

**Phase 1 — Signal scanner complete.**

A working EDGAR Form 4 scanner (`explore_form4.py`) is operational. It pulls
filings for a given date, applies a multi-stage filter pipeline, detects
cluster buys, and outputs ranked signals. Seasonal sampling across four
calendar windows (late Jan, Apr, Jul, Oct) has been validated.

### Empirical findings from initial sampling

- The scanner produces **5–18 qualifying signals per day** after all filters.
- **Cluster buys are rare (0–2 per day)** but appear to be higher quality —
  multiple insiders at the same company buying on the same day is a stronger
  signal than any single purchase.
- **Regional banks and financial companies dominate the signal population.**
  Small-cap financials file more Form 4 purchases than any other sector, which
  means naive follow-all strategies will be heavily sector-concentrated unless
  an explicit sector cap is applied.

Next: backtest pipeline against historical price data to test holding-period
hypotheses, and evaluate whether cluster buys carry significantly different
forward returns than single buys.

## Filter Pipeline (`explore_form4.py`)

Filters applied in order:

| # | Filter | Rule |
|---|---|---|
| 1 | Has purchase transactions | Filing must contain at least one `Code=P` trade |
| 2 | Genuine corporate insider | Position must match known executive roles; no institutional filers |
| 3 | Valid listed ticker | 1–5 uppercase letters, no placeholders |
| 4 | Not a fund or partnership | Excludes names containing `fund`, `trust`, `lp`, `llc`, `reit`, etc. |
| 5 | Filing recency | Reporting period must be within `MAX_FILING_AGE_DAYS` of scan date |
| 6 | Minimum dollar size | Total purchase value ≥ `MIN_PURCHASE_VALUE` |
| 7 | Minimum stock price | Average purchase price ≥ `MIN_STOCK_PRICE` (penny stock filter) |

After filtering, purchases are grouped by ticker. Tickers with ≥ 2 insiders
buying on the same day are flagged as **cluster buys** and printed first.

## Planned Architecture

Agent pipeline (same pattern as the predecessor project):

- **finder** — pull Form 4 filings from EDGAR, filter to buys on listed tickers
- **filter** — apply signal-quality rules (role, cluster detection, size)
- **risk** — position sizing and drawdown management
- **reflector** — trade logging and post-mortem analysis

## Tech Stack

- Python 3.14
- edgartools (SEC EDGAR Form 4 parsing)
- alpaca-py (paper trading execution)
- openai (qualitative signal analysis)
- pandas, python-dotenv

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # fill in your keys
```

Copy `.env.example` to `.env` and populate:

| Variable | Description |
|---|---|
| `ALPACA_API_KEY` | Alpaca paper-trading API key |
| `ALPACA_SECRET_KEY` | Alpaca paper-trading secret |
| `OPENAI_API_KEY` | OpenAI key used by the qualitative filter agent |

Verify the Alpaca connection:

```bash
python -c "from config import get_trading_client; print(get_trading_client().get_account())"
```

## Configuration

Scanner constants in `explore_form4.py`:

| Constant | Default | Description |
|---|---|---|
| `MIN_PURCHASE_VALUE` | `50_000` | Minimum total dollar value of purchases |
| `MIN_STOCK_PRICE` | `2.00` | Minimum average purchase price (penny stock filter) |
| `MAX_FILING_AGE_DAYS` | `5` | Maximum days between reporting period and scan date |
| `SCAN_DATE` | — | Target filing date (YYYY-MM-DD) |

Strategy constants in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `TRADING_MODE` | `"paper"` | Set to `"live"` only after backtest validation |
| `MAX_TRADES_PER_DAY` | `5` | Daily trade cap |
| `RISK_PER_TRADE` | `0.01` | Fraction of equity risked per trade |
| `MAX_DRAWDOWN` | `0.05` | Drawdown limit before trading halts |
| `FORM4_LOOKBACK_DAYS` | `3` | Calendar days of filings to scan |
| `FORM4_MIN_SHARES` | `1000` | Ignore purchases below this share count |

## Output files

| File | Contents |
|---|---|
| `trade_log.json` | One entry per trade with entry reasoning |
| `daily_summaries.json` | Nightly LLM post-mortems |
