# AGENTS.md — form4-tracker

## What this project does

SEC Form 4 insider-buy signal scanner and paper-trading pipeline.
Pulls EDGAR filings → applies rule-based filters → LLM scores each signal →
sizes positions → submits bracket orders to Alpaca paper trading → logs and
post-mortems each trade end-of-day.

## How to run

```bash
# Full pipeline (scan → filter → LLM → size → order)
python explore_form4.py

# Signal scan and filter only (no LLM, no trading — fast, no API keys needed except EDGAR)
python filter_agent.py --date 2025-04-28
```

`--date` defaults to today if omitted. Both entry points accept it.

## Agent pipeline

```
explore_form4.py
  └─ filter_agent.run()
       └─ finder_agent.fetch_raw_trades()     # EDGAR → raw dicts
       └─ filter_agent.filter_trades()         # 7 rule-based filters
       └─ llm_filter_agent.score_candidates()  # GPT-4o scoring, 1–10
       └─ risk_agent.evaluate()                # position sizing, drawdown check
       └─ risk_agent.place_order()             # Alpaca bracket order
       └─ reflector_agent.log_trade()          # persist to trade_log.json
```

`reflector_agent.close_day()` runs separately (e.g. scheduled ~30 min before close)
to reconcile exits and run the GPT-4o-mini post-mortem.

## File map

| File | Role |
|---|---|
| `explore_form4.py` | CLI entry point — validates config, delegates to filter_agent |
| `config.py` | All constants and secrets; call `config.validate()` at startup |
| `finder_agent.py` | EDGAR fetcher — yields raw trade dicts |
| `filter_agent.py` | Rule-based signal filter + CLI runner |
| `llm_filter_agent.py` | GPT-4o scorer — returns scored list sorted by `llm_score` |
| `risk_agent.py` | Position sizing + Alpaca order submission |
| `reflector_agent.py` | Trade logging + end-of-day reconciliation + LLM post-mortem |
| `test_llm.py` | Manual LLM integration test |

## Key design decisions

- **Paper trading only.** `config.get_trading_client()` always passes `paper=True`.
  Do not change this without explicit instruction.

- **All secrets via `.env`.** No credentials are hardcoded. `EDGAR_USER_AGENT`
  is required and must be in the form `"Name email@example.com"` per EDGAR policy.

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
  `trade_log.json`, `daily_summaries.json`, and `hwm.json` will always land next to
  the source files regardless of CWD.

## What to avoid

- Do not call `argparse.parse_args()` at module level — it breaks imports and test runners.
- Do not add `except Exception: pass/continue` without at least logging the exception type.
- Do not add a second OpenAI client instantiation; both `llm_filter_agent` and
  `reflector_agent` use module-level `_client` objects.
- Do not commit `.env` or any file containing actual API keys.

## Environment variables

All four are required for the full pipeline:

```
EDGAR_USER_AGENT    # e.g. "Jane Smith jane@example.com"
OPENAI_API_KEY
ALPACA_API_KEY
ALPACA_SECRET_KEY
```

Call `config.validate()` to get a clear error if any are missing.

## Dependencies

```bash
pip install -r requirements.txt
```

Core: `edgartools`, `openai`, `yfinance`, `alpaca-py`, `pandas`, `python-dotenv`
