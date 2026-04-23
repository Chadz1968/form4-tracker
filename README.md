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

Day 1. Nothing built yet. Currently exploring EDGAR's Form 4 data format
before committing to an architecture.

## Planned Architecture

Agent pipeline (same pattern as the predecessor project):

- **finder** — pull Form 4 filings from EDGAR, filter to buys on S&P 500 tickers
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

Strategy constants are in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `TRADING_MODE` | `"paper"` | Set to `"live"` only after backtest validation |
| `GAP_THRESHOLD` | `0.02` | Minimum gap size to qualify a candidate |
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
