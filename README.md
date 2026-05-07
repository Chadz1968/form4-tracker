# Insider Signals

A research project exploring whether SEC Form 4 insider-buy filings produce
tradeable edge for retail investors.

## Background

This project builds on an earlier gap-momentum trading system which concluded
that retail-accessible price-based signals on S&P 500 names do not produce
durable edge after costs.

The hypothesis here is different: SEC Form 4 filings — legally required
disclosures of insider transactions — may contain predictive information,
particularly for cluster buys, CEO/CFO buys, and non-routine purchases.
Academic literature (Seyhun 1986; Cohen/Malloy/Pomorski 2012) finds roughly
6% annualised alpha for opportunistic insider buys, which is both plausible
for retail and small enough to be credible.

## Research Questions

1. Does a naive "follow insider buys" strategy produce edge after costs on
   S&P 500 names in 2022–2024 backtests?
2. Do cluster buys outperform single-insider buys?
3. Does an LLM-based qualitative filter (role, context, recent news) add
   value over mechanical filtering?
4. Are holding periods of 1 week, 4 weeks, or 12 weeks optimal?

## Status

**Phase 1 — Signal scanner complete. Agent pipeline partially built.**

| Agent | File | Status |
|---|---|---|
| Finder | `finder_agent.py` | Done |
| Filter | `filter_agent.py` | Done |
| Risk | `risk_agent.py` | In progress — not yet wired to Form 4 flow |
| Reflector | `reflector_agent.py` | In progress — not yet wired to Form 4 flow |

### Empirical findings from initial sampling

- The scanner produces **5–18 qualifying signals per day** after all filters.
- **Cluster buys are rare (0–2 per day)** but appear higher quality — multiple
  insiders at the same company buying on the same day is a stronger signal
  than any single purchase.
- **Regional banks and financial companies dominate the signal population.**
  Small-cap financials file more Form 4 purchases than any other sector, so
  naive follow-all strategies will be heavily sector-concentrated unless an
  explicit sector cap is applied.

Next: backtest pipeline against historical price data to test holding-period
hypotheses, and evaluate whether cluster buys carry significantly different
forward returns than single buys.

## Agent Architecture

```
finder_agent  →  filter_agent  →  risk_agent  →  reflector_agent
   (EDGAR)         (signals)       (sizing)         (log + learn)
```

### finder_agent.py

Queries EDGAR for Form 4 filings on a given date. Yields one dict per filing
that contains at least one open-market purchase (Code = P):

```
accession, ticker, company, insider, position, period, p_trades
```

### filter_agent.py

Receives raw trade dicts from the finder and applies the signal-quality filter
pipeline (see below). Qualifying purchases are grouped by ticker; tickers with
≥ 2 insiders buying on the same day are flagged as **cluster buys** and printed
first. Also serves as the CLI entry point.

### risk_agent.py *(in progress)*

Position sizing and drawdown management via Alpaca paper-trading API:
- 1% of account equity risked per trade
- Hard stop at 10% cumulative drawdown
- Bracket orders (entry + stop-loss + take-profit submitted together)

### reflector_agent.py *(in progress)*

End-of-day trade reconciliation and LLM post-mortem:
- Persists each trade to `trade_log.json`
- Reconciles exits against Alpaca fill prices at day close
- Calls OpenAI to generate coaching insights from the day's trades
- Appends daily summary to `daily_summaries.json`

## Filter Pipeline

Filters applied in order inside `filter_agent.py`:

| # | Filter | Rule |
|---|---|---|
| 1 | Has purchase transactions | Filing must contain at least one `Code=P` trade |
| 2 | Genuine corporate insider | Position must match known executive roles; no institutional filers |
| 3 | Valid listed ticker | 1–5 uppercase letters, no placeholders |
| 4 | Not a fund or partnership | Excludes names containing `fund`, `trust`, `lp`, `llc`, `reit`, etc. |
| 5 | Filing recency | Reporting period within `MAX_FILING_AGE_DAYS` of scan date |
| 6 | Minimum dollar size | Total purchase value ≥ `MIN_PURCHASE_VALUE` |
| 7 | Minimum stock price | Average purchase price ≥ `MIN_STOCK_PRICE` (penny stock filter) |

## Usage

```bash
# Canonical entry point
python filter_agent.py --date 2025-04-28

# Legacy wrapper (same output)
python explore_form4.py --date 2025-04-28
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
cp .env.example .env            # fill in your keys
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | For reflector agent | OpenAI key used for post-mortem analysis |
| `ALPACA_API_KEY` | For risk/reflector agents | Alpaca paper-trading API key |
| `ALPACA_SECRET_KEY` | For risk/reflector agents | Alpaca paper-trading secret |
| `EDGAR_USER_AGENT` | Optional | Overrides the hardcoded EDGAR identity string |

The signal scanner (finder + filter) runs without any API keys.

## Configuration

Scanner thresholds in `filter_agent.py`:

| Constant | Default | Description |
|---|---|---|
| `MIN_PURCHASE_VALUE` | `50_000` | Minimum total dollar value of purchases |
| `MIN_STOCK_PRICE` | `2.00` | Minimum average purchase price (penny stock filter) |
| `MAX_FILING_AGE_DAYS` | `5` | Max days between reporting period and scan date |

Strategy and risk constants in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `LOOKBACK_DAYS` | `30` | Cluster-buy detection window |
| `MIN_BUY_DOLLARS` | `50_000` | Minimum purchase size for cluster counting |
| `CLUSTER_MIN_INSIDERS` | `2` | Insiders within lookback window to qualify as cluster |
| `RISK_PER_TRADE` | `0.01` | Fraction of equity risked per trade (1%) |
| `MAX_DRAWDOWN` | `0.10` | Drawdown limit before trading halts (10%) |
| `STOP_PCT` | `0.12` | Stop distance from entry for multi-week holds (12%) |

## Output Files

| File | Contents |
|---|---|
| `trade_log.json` | One entry per trade with entry reasoning and exit fills |
| `daily_summaries.json` | Nightly LLM post-mortems and win/loss stats |

## Tech Stack

- Python 3.14
- edgartools — SEC EDGAR Form 4 parsing
- openai — qualitative post-mortem analysis (reflector agent)
- alpaca-py — paper-trading execution (risk + reflector agents)
- pandas, python-dotenv
