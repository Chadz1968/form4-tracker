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

**Phase 1 — Full agent pipeline built. Paper trading active.**

| Agent | File | Status |
|---|---|---|
| Finder | `finder_agent.py` | Done |
| Filter | `filter_agent.py` | Done |
| LLM Scorer | `llm_filter_agent.py` | Done |
| Risk | `risk_agent.py` | Done |
| Reflector | `reflector_agent.py` | Done |

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
finder_agent  →  filter_agent  →  llm_filter_agent  →  risk_agent  →  reflector_agent
   (EDGAR)         (signals)          (LLM scoring)      (sizing)       (log + learn)
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
≥ 2 insiders buying on the same day are flagged as **cluster buys**.
Also serves as the CLI entry point when run directly.

### llm_filter_agent.py

Scores qualifying signals across five dimensions using a mix of deterministic
Python logic and GPT-4o structured output:

| Dimension | Weight | Method |
|---|---|---|
| Conviction | 25% | LLM — purchase size vs. estimated role compensation |
| Timing | 25% | LLM — buying into price weakness (contrarian) |
| Role | 20% | Deterministic — CEO/CFO/Chairman score higher |
| Cluster | 15% | Deterministic — multiple insiders amplifies conviction |
| Thesis | 15% | LLM — sector-aware valuation and context |

Each dimension scores 1–3; composite is normalised to 1–10. Signals below
`MIN_LLM_SCORE` (default 4.0) are dropped before position sizing.

### risk_agent.py

Position sizing and drawdown management via Alpaca paper-trading API:
- 1% of account equity risked per trade
- Hard stop at 10% cumulative drawdown (measured from all-time equity peak)
- Stop: 12% below entry; target: 24% above entry (2:1 R/R)
- Bracket orders (entry + stop-loss + take-profit submitted together, children GTC)

### reflector_agent.py

End-of-day trade reconciliation and LLM post-mortem:
- Persists each trade to `trade_log.json` at entry
- Reconciles exits against Alpaca bracket-leg fills at day close
- Calls GPT-4o-mini to generate coaching insights from the day's trades
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
# Full pipeline: scan → filter → LLM score → size → order
python explore_form4.py

# Scan and filter only (no LLM, no trading)
python filter_agent.py --date 2025-04-28

# Specify a date (both entry points accept --date)
python explore_form4.py --date 2025-04-28
```

`--date` defaults to today if omitted.

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
| `EDGAR_USER_AGENT` | Yes | Identity string sent to EDGAR (e.g. `"Your Name email@example.com"`) |
| `OPENAI_API_KEY` | Yes | GPT-4o scoring in `llm_filter_agent`; GPT-4o-mini post-mortem in `reflector_agent` |
| `ALPACA_API_KEY` | Yes | Alpaca paper-trading API key |
| `ALPACA_SECRET_KEY` | Yes | Alpaca paper-trading secret |

All four are required for the full pipeline. `finder_agent` and `filter_agent`
only need `EDGAR_USER_AGENT`.

## Configuration

Scanner thresholds in `filter_agent.py`:

| Constant | Default | Description |
|---|---|---|
| `MIN_PURCHASE_VALUE` | `50_000` | Minimum total dollar value of purchases |
| `MIN_STOCK_PRICE` | `2.00` | Minimum average purchase price (penny stock filter) |
| `MAX_FILING_AGE_DAYS` | `5` | Max days between reporting period and scan date |

LLM filter threshold in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `MIN_LLM_SCORE` | `4.0` | Minimum composite score (1–10) to pass to risk agent |

Risk constants in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `RISK_PER_TRADE` | `0.01` | Fraction of equity risked per trade (1%) |
| `MAX_DRAWDOWN` | `0.10` | Drawdown limit from equity peak before trading halts (10%) |
| `STOP_PCT` | `0.12` | Stop distance from entry for multi-week holds (12%) |

## Output Files

| File | Contents |
|---|---|
| `trade_log.json` | One entry per trade with entry reasoning and exit fills |
| `daily_summaries.json` | Nightly LLM post-mortems and win/loss stats |
| `hwm.json` | Persistent equity high-water mark for drawdown tracking |

## Tech Stack

- Python 3.14
- edgartools — SEC EDGAR Form 4 parsing
- openai — LLM scoring (GPT-4o) and post-mortem analysis (GPT-4o-mini)
- yfinance — price context for LLM scoring
- alpaca-py — paper-trading execution
- pandas, python-dotenv
