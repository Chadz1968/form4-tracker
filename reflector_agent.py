"""
reflector_agent.py

Two responsibilities:

  log_trade(trade, order)  — called immediately after place_order().
      Persists one record to trade_log.json capturing the full insider signal
      context at entry: insider name, role, cluster size, all LLM dimension
      scores, reasoning, and Alpaca order details.

  close_day()  — called once per day (e.g. 30 min before close).
      Reconciles any exits that filled (stop hit or target hit) against Alpaca,
      updates P&L, then runs an LLM post-mortem that asks which signal
      characteristics predicted outcomes — feeding back into filter refinement.
      Insider positions are multi-week holds, so most trades will still be open
      when close_day() runs; it only updates the ones that actually closed.

Output files:
  trade_log.json       — one entry per trade, updated in place on exit
  daily_summaries.json — appended each time close_day() runs
"""

import json
import os
from datetime import date, datetime

from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from openai import OpenAI

from config import get_trading_client, OPENAI_KEY

TRADE_LOG   = "trade_log.json"
SUMMARY_LOG = "daily_summaries.json"


# ── File helpers ──────────────────────────────────────────────

def _load_json(path: str) -> list:
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return []


def _save_json(path: str, data: list) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Entry logging ─────────────────────────────────────────────

def log_trade(trade: dict, order: dict) -> None:
    """
    Persist a trade entry at the moment of order submission.

    Args:
        trade: approved trade dict from risk_agent.evaluate() — contains all
               signal fields plus entry_price, stop_price, target_price, shares
        order: return value of risk_agent.place_order() — contains 'id' and
               'target_price'
    """
    cluster_members = trade.get("cluster_members") or []
    member_summary  = [
        f"{m['insider']} ({m['position']}): ${m['value']:,.0f}"
        for m in cluster_members
    ] if cluster_members else []

    entry = {
        # ── Identity ──────────────────────────────────────────
        "date":             str(date.today()),
        "timestamp":        datetime.utcnow().isoformat(),
        "ticker":           trade["ticker"],
        "company":          trade.get("company", ""),

        # ── Insider signal ────────────────────────────────────
        "insider":          trade.get("insider", ""),
        "position":         trade.get("position", ""),
        "cluster_size":     trade.get("cluster_size", 1),
        "cluster_members":  member_summary,
        "insider_value":    trade.get("value"),       # total $ insiders bought
        "insider_avg_price":trade.get("avg_price"),   # price they paid

        # ── LLM scores ────────────────────────────────────────
        "llm_score":        trade.get("llm_score"),
        "role_score":       trade.get("role_score"),
        "conviction_score": trade.get("conviction_score"),
        "timing_score":     trade.get("timing_score"),
        "cluster_score":    trade.get("cluster_score"),
        "thesis_score":     trade.get("thesis_score"),
        "reasoning":        trade.get("reasoning", ""),
        "red_flags":        trade.get("red_flags", []),

        # ── Trade sizing ──────────────────────────────────────
        "shares":           trade["shares"],
        "entry_price":      trade["entry_price"],
        "stop_price":       trade["stop_price"],
        "target_price":     order.get("target_price"),
        "dollar_risk":      trade["dollar_risk"],
        "account_equity":   trade.get("account_equity"),
        "order_id":         order.get("id"),

        # ── Exit (populated by close_day / update_exit) ───────
        "exit_price":       None,
        "exit_reason":      None,
        "holding_days":     None,
        "pnl":              None,
        "pnl_pct":          None,
        "outcome":          "open",
    }

    log = _load_json(TRADE_LOG)
    log.append(entry)
    _save_json(TRADE_LOG, log)
    cluster_tag = f" [CLUSTER x{entry['cluster_size']}]" if entry["cluster_size"] > 1 else ""
    print(f"[Reflector] Logged: {entry['ticker']}{cluster_tag} | "
          f"{entry['insider']} | score={entry['llm_score']} | "
          f"{entry['shares']} shares @ ${entry['entry_price']}")


# ── Exit reconciliation ───────────────────────────────────────

def update_exit(order_id: str, exit_price: float, exit_reason: str) -> None:
    """Update an open trade record with exit fill details and compute P&L."""
    log = _load_json(TRADE_LOG)
    for entry in log:
        if entry.get("order_id") == order_id and entry["outcome"] == "open":
            entry_date  = datetime.strptime(entry["date"], "%Y-%m-%d").date()
            holding     = (date.today() - entry_date).days
            pnl         = round(
                (exit_price - entry["entry_price"]) * entry["shares"], 2
            )
            pnl_pct     = round(
                (exit_price - entry["entry_price"]) / entry["entry_price"] * 100, 2
            )
            entry.update({
                "exit_price":   exit_price,
                "exit_reason":  exit_reason,
                "holding_days": holding,
                "pnl":          pnl,
                "pnl_pct":      pnl_pct,
                "outcome":      "win" if pnl > 0 else "loss",
            })
            break
    _save_json(TRADE_LOG, log)


def _fetch_exit_fills(client, open_trades: list[dict]) -> dict[str, tuple[float, str]]:
    """
    For each open trade, check its Alpaca bracket order for a filled child leg.
    Falls back to scanning all filled orders for a same-symbol flatten.

    Returns {order_id: (exit_price, reason)} where reason is 'stop', 'target',
    or 'flatten'.
    """
    results: dict[str, tuple[float, str]] = {}

    try:
        all_orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=200)
        )
    except Exception as e:
        print(f"[Reflector] Could not fetch order list: {e}")
        all_orders = []

    fills_by_symbol: dict[str, list] = {}
    for o in all_orders:
        if str(o.status) == "filled" and o.filled_avg_price:
            fills_by_symbol.setdefault(o.symbol, []).append(o)

    for trade in open_trades:
        parent_id = trade.get("order_id")
        if not parent_id:
            continue

        # Primary: check bracket child legs
        try:
            parent = client.get_order_by_id(parent_id)
            for leg in (parent.legs or []):
                if str(leg.status) == "filled" and leg.filled_avg_price:
                    reason = "stop" if "stop" in str(leg.order_type).lower() else "target"
                    results[parent_id] = (float(leg.filled_avg_price), reason)
                    break
            if parent_id in results:
                continue
        except Exception as e:
            print(f"[Reflector] Could not fetch order {parent_id}: {e}")

        # Fallback: manual flatten — opposite-side market fill
        for o in fills_by_symbol.get(trade["ticker"], []):
            if str(o.side).lower() == "sell" and str(o.order_type).lower() == "market":
                results[parent_id] = (float(o.filled_avg_price), "flatten")
                break

    return results


# ── Daily close ───────────────────────────────────────────────

def close_day() -> dict:
    """
    Reconcile exits, run post-mortem, append to daily_summaries.json.

    Safe to call daily even when most positions are still open — only trades
    with a filled exit leg are updated. Open multi-week holds are left as-is.
    """
    print("[Reflector] Running end-of-day reconciliation...")

    client      = get_trading_client()
    log         = _load_json(TRADE_LOG)
    open_trades = [t for t in log if t["outcome"] == "open"]
    exits       = _fetch_exit_fills(client, open_trades)

    for entry in open_trades:
        pid = entry.get("order_id")
        if pid and pid in exits:
            exit_price, exit_reason = exits[pid]
            update_exit(pid, exit_price, exit_reason)

    trades_today = _collect_todays_trades()
    all_open     = [t for t in _load_json(TRADE_LOG) if t["outcome"] == "open"]

    summary                = _build_summary(trades_today, all_open)
    summary["insights"]    = _run_postmortem(trades_today, all_open)

    summaries = _load_json(SUMMARY_LOG)
    summaries.append(summary)
    _save_json(SUMMARY_LOG, summaries)

    print(f"[Reflector] Done. Today={summary['trades_today']} trades | "
          f"Closed today={summary['closed_today']} | "
          f"P&L=${summary['total_pnl']} | "
          f"Open positions={summary['open_positions']}")
    if summary["insights"]:
        print(f"[Reflector] Insights: {summary['insights'][:120]}...")
    return summary


def _collect_todays_trades() -> list[dict]:
    today = str(date.today())
    return [t for t in _load_json(TRADE_LOG) if t.get("date") == today]


def _build_summary(trades_today: list[dict], all_open: list[dict]) -> dict:
    closed  = [t for t in trades_today if t["outcome"] != "open"]
    wins    = sum(1 for t in closed if t["outcome"] == "win")
    losses  = sum(1 for t in closed if t["outcome"] == "loss")
    pnl     = round(sum(t["pnl"] for t in closed if t["pnl"] is not None), 2)
    return {
        "date":            str(date.today()),
        "trades_today":    len(trades_today),
        "closed_today":    len(closed),
        "wins":            wins,
        "losses":          losses,
        "win_rate":        round(wins / len(closed), 2) if closed else None,
        "total_pnl":       pnl,
        "open_positions":  len(all_open),
    }


def _run_postmortem(trades_today: list[dict], all_open: list[dict]) -> str:
    """
    Ask the LLM which signal characteristics predicted outcomes and what
    filter threshold adjustments are worth testing.
    """
    closed = [t for t in trades_today if t["outcome"] != "open"]
    if not closed and not all_open:
        return "No trades on record — nothing to analyse."

    def fmt(t: dict) -> str:
        outcome = t["outcome"]
        pnl_str = f"pnl=${t['pnl']:+.0f} ({t.get('pnl_pct', '?'):+.1f}%)" if t["pnl"] is not None else "still open"
        return (
            f"- {t['ticker']} | {t['insider']} ({t['position']}) | "
            f"cluster={t['cluster_size']} | score={t['llm_score']} | "
            f"role={t['role_score']} conviction={t['conviction_score']} "
            f"timing={t['timing_score']} thesis={t['thesis_score']} | "
            f"held {t.get('holding_days', '?')} days | exit={t.get('exit_reason','?')} | "
            f"{pnl_str}"
        )

    closed_block = "\n".join(fmt(t) for t in closed) if closed else "None closed today."
    open_block   = "\n".join(
        f"- {t['ticker']} | score={t['llm_score']} | "
        f"entry=${t['entry_price']} stop=${t['stop_price']} target=${t['target_price']} | "
        f"held {(date.today() - datetime.strptime(t['date'], '%Y-%m-%d').date()).days} days"
        for t in all_open
    ) if all_open else "None."

    prompt = f"""You are an insider-signal trading analyst reviewing performance data.
Your job is to identify which signal characteristics predicted trade outcomes and
suggest specific, testable adjustments to the scoring thresholds.

TRADES CLOSED TODAY:
{closed_block}

OPEN POSITIONS (multi-week holds, not yet closed):
{open_block}

In 3-5 bullet points answer:
1. Which scoring dimensions (role, conviction, timing, cluster, thesis) best separated
   winners from losers in closed trades?
2. Is the MIN_LLM_SCORE threshold (currently 4.0/10) set correctly, or should it move?
3. Any pattern in exit reason (stop vs target) that suggests the stop/target distances
   need adjustment?
4. One specific, testable rule change for tomorrow's filter.

Be direct. Reference specific tickers and scores. This goes to a portfolio manager."""

    client   = OpenAI(api_key=OPENAI_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
    )
    return response.choices[0].message.content.strip()
