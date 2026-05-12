# CLAUDE.md — form4-tracker

## What this project does

SEC Form 4 insider-buy signal scanner, backtest engine, and paper-trading pipeline.
Pulls EDGAR filings → applies rule-based filters → LLM scores each signal →
sizes positions → submits bracket orders to Alpaca paper trading → logs and
post-mortems each trade end-of-day.

## How to run

```bash
# Signal scan + filter only (finder + filter pipeline — fast, only EDGAR_USER_AGENT needed)
python explore_form4.py
python filter_agent.py --date 2025-04-28     # same thing, direct CLI

# Finder + filter + LLM scoring (no trading — requires OPENAI_API_KEY)
python test_llm.py --date 2025-04-28

# End-of-day reconciliation + LLM post-mortem (run ~30 min before market close)
# Call reflector_agent.close_day() — no standalone CLI, import and call directly

# Historical backtest (EDGAR_USER_AGENT only; no OpenAI/Alpaca needed)
python backtest_agent.py --start 2022-01-01 --end 2024-12-31
python backtest_agent.py --start 2022-01-01 --end 2024-12-31 --resume
python backtest_agent.py --returns-only --end 2024-12-31
python backtest_agent.py --start 2024-01-01 --end 2024-01-31 --max-days 10
```

`--date` defaults to today if omitted on `explore_form4.py` and `filter_agent.py`.

## Agent pipeline

The agents are implemented end-to-end but not all wired into a single CLI entry point.
`explore_form4.py` runs the **finder → filter** leg only:

```
explore_form4.py (or filter_agent.py --date)
  └─ filter_agent.run()
       └─ finder_agent.fetch_raw_trades()     # EDGAR → raw dicts (with disk cache)
       └─ filter_agent.filter_trades()         # 7 rule-based filters
       (prints signal report; no LLM/trading)
```

`test_llm.py` exercises the **finder → filter → LLM** leg:

```
test_llm.py
  └─ filter_agent.get_candidates()            # finder + filter
  └─ llm_filter_agent.score_candidates()      # GPT-4o scoring, 1–10
  (prints scored signals; no trading)
```

The **risk and reflector** agents are complete and designed to be called after LLM scoring:

```
risk_agent.evaluate(candidates)              # position sizing, drawdown check
  └─ risk_agent.place_order(trade)           # Alpaca bracket order
       └─ reflector_agent.log_trade()        # persist to trade_log.json

reflector_agent.close_day()                 # runs separately ~30 min before close
```

`backtest_agent.py` runs the finder+filter pipeline over historical dates without LLM/trading:

```
backtest_agent.py
  Phase 1: collect_signals() — scans each trading day, writes backtest_signals.json
  Phase 2: build_results()   — bulk-downloads prices via yfinance, writes backtest_results.csv
```

## File map

| File | Role |
|---|---|
| `explore_form4.py` | CLI entry point — validates config, runs finder+filter, prints signal report |
| `config.py` | All constants and secrets; call `config.validate()` at startup |
| `finder_agent.py` | EDGAR fetcher — yields raw trade dicts; caches past dates to `cache/` |
| `filter_agent.py` | Rule-based signal filter + CLI runner |
| `llm_filter_agent.py` | GPT-4o scorer — returns scored list sorted by `llm_score` |
| `risk_agent.py` | Position sizing + Alpaca order submission |
| `reflector_agent.py` | Trade logging + end-of-day reconciliation + LLM post-mortem |
| `backtest_agent.py` | Historical signal collection + forward-return computation vs SPY |
| `test_llm.py` | Manual LLM integration test (finder + filter + LLM scoring) |
| `backtest_analysis.ipynb` | Exploratory analysis of backtest results |
| `edgar_exploration.ipynb` | Early EDGAR API exploration notebook |

## Key design decisions

- **Paper trading only.** `config.get_trading_client()` always passes `paper=True`.
  Do not change this without explicit instruction.

- **All secrets via `.env`.** No credentials are hardcoded. `EDGAR_USER_AGENT`
  is required and must be in the form `"Name email@example.com"` per EDGAR policy.

- **finder_agent disk cache.** Past-date results are cached to `cache/edgar_raw_YYYY-MM-DD.json`
  automatically. Today's results are re-fetched unless `FORM4_USE_TODAY_CACHE=1`.
  This makes backtest reruns and date re-scans fast. Cache version is embedded in the
  payload; a version mismatch causes a fresh fetch.

- **Concurrent EDGAR fetching.** `finder_agent` uses a `ThreadPoolExecutor` with
  `FORM4_EDGAR_WORKERS` workers (default 6). Each filing.obj() call runs on a daemon
  thread with a hard 30-second timeout (`_FILING_TIMEOUT`); stalled EDGAR network
  calls are abandoned rather than blocking the pool.

- **Drawdown is measured from all-time equity peak**, persisted in `hwm.json`.
  `risk_agent._drawdown_ok()` reads and updates this file. Do not delete `hwm.json`
  mid-session or the drawdown guard resets.

- **LLM scoring uses GPT-4o** (not a cheaper model) because conviction/timing/thesis
  scoring requires nuanced sector-aware reasoning. The post-mortem in `reflector_agent`
  uses GPT-4o-mini as quality requirements are lower there.

- **Bracket orders, children GTC.** Both the stop-loss and take-profit legs persist
  until one fills. Do not change `TimeInForce` without also updating `reflector_agent`'s
  exit-reconciliation logic.

- **Log files are pinned to the project directory** (`os.path.dirname(__file__)`).
  `trade_log.json`, `daily_summaries.json`, `hwm.json`, `backtest_signals.json`, and
  `backtest_results.csv` will always land next to the source files regardless of CWD.

- **Backtest uses finder+filter only (no LLM).** `backtest_agent` calls
  `filter_agent.get_candidates()` directly. LLM scoring is excluded to keep
  historical scans fast and avoid OpenAI costs across thousands of dates.

## What to avoid

- Do not call `argparse.parse_args()` at module level — it breaks imports and test runners.
  (Note: `test_llm.py` currently violates this; do not replicate the pattern.)
- Do not add `except Exception: pass/continue` without at least logging the exception type.
- Do not add a second OpenAI client instantiation; both `llm_filter_agent` and
  `reflector_agent` use module-level `_client` objects.
- Do not commit `.env` or any file containing actual API keys.

## Environment variables

Required for the full pipeline:

```
EDGAR_USER_AGENT    # e.g. "Jane Smith jane@example.com" — required for ALL entry points
OPENAI_API_KEY      # required for llm_filter_agent and reflector_agent
ALPACA_API_KEY      # required for risk_agent and reflector_agent
ALPACA_SECRET_KEY   # required for risk_agent and reflector_agent
```

Optional tuning:

```
FORM4_EDGAR_WORKERS      # parallel EDGAR fetch workers (default: 6)
FORM4_USE_TODAY_CACHE    # set to "1" or "true" to read today's results from cache
```

Call `config.validate()` to get a clear error if any required vars are missing.

## Key constants

Signal filter thresholds in `filter_agent.py`:

| Constant | Default | Description |
|---|---|---|
| `MIN_PURCHASE_VALUE` | `50_000` | Minimum total dollar value of purchases |
| `MIN_STOCK_PRICE` | `2.00` | Minimum average purchase price (penny stock filter) |
| `MAX_FILING_AGE_DAYS` | `5` | Max days between reporting period and scan date |

Cluster / LLM thresholds in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `MIN_LLM_SCORE` | `4.0` | Minimum composite score (1–10) to pass to risk agent |
| `LOOKBACK_DAYS` | `30` | Cluster-buy detection window |
| `CLUSTER_MIN_INSIDERS` | `2` | Insiders required to qualify as a cluster |

Risk constants in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `RISK_PER_TRADE` | `0.01` | Fraction of equity risked per trade (1%) |
| `MAX_DRAWDOWN` | `0.10` | Drawdown limit from equity peak before trading halts (10%) |
| `STOP_PCT` | `0.12` | Stop distance from entry (12% for multi-week holds) |

## Dependencies

```bash
pip install -r requirements.txt
```

Core: `edgartools`, `openai`, `yfinance`, `alpaca-py`, `pandas`, `python-dotenv`

Backtest / analysis: `matplotlib`, `scipy`, `jupyter`
