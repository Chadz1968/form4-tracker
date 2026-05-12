# Day Trading Manager Coach Roadmap

## Product Goal

Build a trading manager and coach that helps a novice trader develop a repeatable,
measurable day-trading process using TradingView for chart alerts and Alpaca for
paper trading, execution data, positions, fills, and account state.

The first job of the system is not to find magic trades. It is to enforce a
professional workflow:

```
market context -> watchlist -> setup -> trigger -> risk check -> execution
-> journal -> review -> rule improvement
```

The app should behave as a coach before it behaves as an auto-trader. It should
block weak trades, size accepted trades conservatively, record every decision,
and learn which setups actually work for the user over time.

## Principles To Emulate

The project should borrow from the common habits of strong discretionary and
systematic traders, not copy any single person's strategy blindly.

| Model | What to emulate | App behavior |
|---|---|---|
| Momentum day trading | Trade stocks that are already moving on volume, preferably with a catalyst | Prefer high relative volume, clean trend, liquidity, and news-driven names |
| VWAP / opening range traders | Use simple intraday reference levels and avoid random entries | Require a named technical trigger before a trade is allowed |
| Risk-first traders | Decide the invalidation point before entry | Compute position size from planned stop and max daily risk |
| Playbook traders | Specialize in a few repeatable setups | Tag every trade with a setup and measure expectancy by setup |
| Post-analysis traders | Review winners and losers to refine rules | Produce daily and weekly coaching notes from the journal |
| Progressive exposure traders | Increase risk only when current behavior is working | Start tiny in paper, then adjust max risk only after verified results |

## Implementation Status

| Phase | Status | Implemented files |
|---|---|---|
| Phase 0 - Safety And Scope | Built | `trading_policy.json`, `risk_rules.json`, `playbook.json` |
| Phase 1 - Broker And Data Foundation | Built | `trade_manager_journal.py`, `alpaca_reconciliation.py` |
| Phase 2 - TradingView Signal Inbox | Built | `tradingview_webhook.py`, `signal_inbox.json` |
| Phase 3 - Playbook And Trade Approval | Built | `trade_manager_journal.py`, `trade_manager_ui.py` |
| Phase 4 - Paper Execution With Alpaca | Pending | Approved plan -> Alpaca order submission still pending |
| Phase 5 - Coaching And Review Engine | Partial | Daily review helper exists; weekly/self-learning reviews pending |
| Phase 6 - Self-Learning Rules | Pending | Rule proposal engine pending |
| Phase 7 - Optional Signal Sources | Pending | News/social scanners pending |

## Phase 0 - Safety And Scope

**Milestone:** The project has a written safety policy and runs paper-only.

Requirements:

- Alpaca is paper-only until explicitly changed.
- No fully automated live trading during the learning phase.
- The system must know the user's daily max loss, max trades, max loss streak,
  and allowed setups.
- Every trade must have a planned entry, stop, target, and setup tag before the
  system can approve it.
- The system should support a manual "coach mode" where it says allow, reject,
  or review, without placing any order.

Outputs:

- `trading_policy.json`
- `playbook.json`
- `risk_rules.json`

Exit criteria:

- A trade can be evaluated against risk rules without touching a broker.
- The system can explain why a trade was accepted or rejected.

## Phase 1 - Broker And Data Foundation

**Milestone:** Alpaca account, orders, positions, and fills are normalized into a
local trading journal.

Inputs:

- Alpaca account equity
- Alpaca open positions
- Alpaca orders and fills
- Manual trade notes

Core data model:

| Entity | Purpose |
|---|---|
| `Signal` | A possible trade idea or alert |
| `TradePlan` | Entry, stop, target, setup, risk, and reason |
| `OrderFill` | Broker execution record |
| `TradeJournalEntry` | Full lifecycle record from idea to result |
| `DailyReview` | Day-level performance, behavior, and coaching notes |
| `SetupStats` | Win rate, average R, expectancy, and mistake rate by setup |

Outputs:

- `trade_journal.json` or SQLite table
- `daily_reviews.json` or SQLite table
- `setup_stats.json` or SQLite table

Exit criteria:

- Open and closed Alpaca trades can be reconciled.
- P&L, R-multiple, holding time, setup, and mistake tags are stored.
- Manual trades can be added even if Alpaca was not used for execution.

## Phase 2 - TradingView Signal Inbox

**Milestone:** TradingView alerts can arrive as structured signals, but the app
does not automatically trade them.

TradingView webhook payload shape:

```json
{
  "source": "tradingview",
  "symbol": "AAPL",
  "timeframe": "5m",
  "setup": "vwap_reclaim",
  "side": "long",
  "price": 190.25,
  "trigger": "close_above_vwap",
  "notes": "High relative volume and market aligned"
}
```

Signal categories:

- `watchlist`: candidate only, no trade yet
- `setup_ready`: setup is forming
- `entry_trigger`: entry condition fired
- `exit_trigger`: stop, target, or exit rule fired

Exit criteria:

- The app can receive, validate, and store alerts.
- Duplicate or malformed alerts are rejected safely.
- Alerts are visible in a signal inbox with status: new, planned, rejected,
  expired, or traded.

## Phase 3 - Playbook And Trade Approval

**Milestone:** The app knows the user's approved setups and grades every trade
before execution.

Starter playbook:

| Setup | Description | Required conditions |
|---|---|---|
| Opening range breakout | Price breaks the first 5- or 15-minute range with volume | Relative volume high, market aligned, stop below range |
| VWAP reclaim | Price reclaims VWAP after weakness and holds | Clean reclaim, volume confirmation, defined stop |
| VWAP pullback | Trending stock pulls back to VWAP and holds | Trend intact, controlled pullback, favorable risk/reward |
| News momentum | Stock moves on fresh catalyst and volume | Catalyst present, liquid ticker, spread acceptable |
| Failed breakdown reversal | Price breaks support then quickly reclaims it | Clear trap, volume confirmation, tight invalidation |

Trade approval checks:

- Is this setup in the approved playbook?
- Is the market regime suitable?
- Is there a clear catalyst or technical reason?
- Is the spread acceptable?
- Is relative volume high enough?
- Is the planned reward at least 2R?
- Is the stop logical and not arbitrary?
- Does the position size fit max risk?
- Has the daily loss limit or max trade limit been hit?
- Is this a revenge trade or low-quality chase?

Exit criteria:

- Every trade gets an `A`, `B`, `C`, or `Reject` grade.
- Rejected trades include a clear coaching reason.
- Approved trades produce a position size and bracket order proposal.

Implementation note:

- `trade_manager_ui.py` provides the first local review desk for selecting a
  TradingView signal, entering entry/stop/target, running the coach check, and
  saving the plan to `trade_journal.json`.

## Phase 4 - Paper Execution With Alpaca

**Milestone:** Approved trades can be sent to Alpaca paper trading with bracket
orders and then reconciled back into the journal.

Order rules:

- Use bracket orders when supported.
- Stop is required.
- Target is required unless the playbook explicitly allows a trailing exit.
- Size is based on risk per trade, not confidence alone.
- No new trade after daily stop is hit.
- No averaging down.
- No moving stop farther away after entry.

Exit criteria:

- Accepted trade plans can place paper orders.
- Fills update the journal automatically.
- Stops, targets, partial exits, and manual closes are recorded.
- The app can compare planned entry with actual fill to measure slippage.

## Phase 5 - Coaching And Review Engine

**Milestone:** The system produces daily and weekly reviews that teach the user
what to do more of and what to stop doing.

Daily review:

- Total P&L
- Net P&L after estimated costs
- Number of trades
- Win rate
- Average win, average loss
- Average R
- Best setup
- Worst setup
- Rule breaks
- Revenge-trade warnings
- Overtrading warnings
- One coaching focus for tomorrow

Weekly review:

- Expectancy by setup
- P&L by time of day
- P&L by ticker
- P&L by market regime
- P&L by setup grade
- Most common mistake tag
- Biggest avoidable loss
- Best executed trade
- Rule changes proposed for next week

Exit criteria:

- The app can say which setup is currently worth focusing on.
- The app can identify conditions where the user should stop trading.
- The app can separate good losing trades from bad winning trades.

## Phase 6 - Self-Learning Rules

**Milestone:** The system learns from results, but changes behavior gradually and
with user approval.

Allowed learning:

- Suggest raising or lowering setup priority.
- Suggest avoiding specific times of day.
- Suggest tightening or widening stop templates after enough evidence.
- Suggest removing setups with negative expectancy.
- Suggest reducing risk after drawdown or rule breaks.
- Suggest increasing paper risk only after consistent rule-following.

Not allowed at first:

- Fully autonomous live trading.
- Increasing risk because of a short winning streak.
- Adding new strategies from social media without backtesting or paper evidence.
- Hiding rule changes from the user.

Evidence thresholds:

- Minimum 20 trades before judging a setup.
- Minimum 50 trades before making strong conclusions.
- Minimum 4 weeks of paper data before considering live execution.
- Every recommendation must include the supporting stats.

Exit criteria:

- The app can generate a weekly rule-change proposal.
- The user can approve, reject, or defer each proposed change.
- All rule changes are versioned.

## Phase 7 - Optional Signal Sources Beyond TradingView

**Milestone:** Add more signal inputs only after the core coach works.

Possible inputs:

- News catalyst feed
- Earnings calendar
- Premarket gap scanner
- Unusual volume scanner
- Social sentiment from Reddit or X
- Existing Form 4 insider signals for swing-trade context

Rules:

- Social signals are context, not trade triggers.
- News signals create watchlist candidates, not automatic buys.
- Technical confirmation and risk approval are still required.
- Every signal source must be measured for usefulness.

Exit criteria:

- The system can report whether a source improved results.
- Low-quality sources can be disabled.

## Recommended Build Order

1. Create durable journal schema.
2. Add Alpaca import/reconciliation for account, orders, positions, and fills.
3. Add manual trade entry and review fields.
4. Add playbook and risk policy files.
5. Add trade approval engine.
6. Add TradingView webhook signal inbox.
7. Add Alpaca paper order submission from approved trade plans.
8. Add daily review report.
9. Add weekly review report.
10. Add self-learning rule proposals.

## Definition Of Done

The project is working when a full trading day can run like this:

1. The user opens the app before market open.
2. The app imports account state from Alpaca.
3. TradingView alerts populate a signal inbox.
4. The user selects a signal and creates a trade plan.
5. The coach accepts or rejects the plan with reasons.
6. Accepted plans can be sent to Alpaca paper trading.
7. Fills and exits are reconciled automatically.
8. The user adds notes and mistake tags.
9. The app produces an end-of-day coaching report.
10. Weekly review proposes rule improvements based on actual evidence.

## Initial Success Metrics

- 100% of trades have a setup tag.
- 100% of trades have a planned stop before entry.
- 100% of trades have an R-multiple result.
- Fewer than 5% of trades violate risk rules.
- No trading after daily max loss.
- At least 20 paper trades logged before judging any setup.
- At least 4 weeks of paper evidence before considering live execution.
