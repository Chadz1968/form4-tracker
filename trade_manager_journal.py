"""
trade_manager_journal.py

Phase 0/1 foundation for the day trading manager coach.

This module is deliberately broker-independent. It stores TradingView-style
signals, validates trade plans against the local playbook and risk policy, and
records trade lifecycle data. Alpaca execution can be wired on top later.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any


_DIR = os.path.dirname(os.path.abspath(__file__))

TRADING_POLICY_FILE = os.path.join(_DIR, "trading_policy.json")
RISK_RULES_FILE = os.path.join(_DIR, "risk_rules.json")
PLAYBOOK_FILE = os.path.join(_DIR, "playbook.json")
SIGNAL_INBOX_FILE = os.path.join(_DIR, "signal_inbox.json")
TRADE_JOURNAL_FILE = os.path.join(_DIR, "trade_journal.json")
DAILY_REVIEWS_FILE = os.path.join(_DIR, "manager_daily_reviews.json")

VALID_SIGNAL_STATUSES = {"new", "planned", "rejected", "expired", "traded"}
VALID_TRADE_OUTCOMES = {"planned", "open", "win", "loss", "breakeven", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_trading_policy() -> dict:
    return _load_json(TRADING_POLICY_FILE, {})


def load_risk_rules() -> dict:
    return _load_json(RISK_RULES_FILE, {})


def load_playbook() -> dict:
    return _load_json(PLAYBOOK_FILE, {"approved_setups": []})


def approved_setup_ids(playbook: dict | None = None) -> set[str]:
    playbook = playbook or load_playbook()
    return {s["id"] for s in playbook.get("approved_setups", [])}


def ingest_signal(payload: dict) -> dict:
    """
    Store a TradingView-style signal in the inbox.

    Required fields: source, symbol, setup, side, price.
    """
    required = ["source", "symbol", "setup", "side", "price"]
    missing = [field for field in required if payload.get(field) in (None, "")]
    if missing:
        raise ValueError(f"Signal missing required fields: {', '.join(missing)}")

    signal = {
        "id": str(uuid.uuid4()),
        "created_at": _utc_now(),
        "status": "new",
        "source": str(payload["source"]).lower(),
        "symbol": str(payload["symbol"]).upper(),
        "timeframe": payload.get("timeframe"),
        "setup": payload["setup"],
        "side": str(payload["side"]).lower(),
        "price": float(payload["price"]),
        "trigger": payload.get("trigger"),
        "notes": payload.get("notes", ""),
        "levels": payload.get("levels") or {},
        "raw_payload": payload,
    }

    inbox = _load_json(SIGNAL_INBOX_FILE, [])
    inbox.append(signal)
    _save_json(SIGNAL_INBOX_FILE, inbox)
    return signal


def update_signal_status(signal_id: str, status: str) -> None:
    if status not in VALID_SIGNAL_STATUSES:
        raise ValueError(f"Invalid signal status: {status}")
    inbox = _load_json(SIGNAL_INBOX_FILE, [])
    for signal in inbox:
        if signal["id"] == signal_id:
            signal["status"] = status
            signal["updated_at"] = _utc_now()
            _save_json(SIGNAL_INBOX_FILE, inbox)
            return
    raise ValueError(f"Signal not found: {signal_id}")


def reward_to_risk(entry_price: float, stop_price: float, target_price: float, side: str) -> float:
    if side == "long":
        risk = entry_price - stop_price
        reward = target_price - entry_price
    elif side == "short":
        risk = stop_price - entry_price
        reward = entry_price - target_price
    else:
        raise ValueError(f"Unsupported side: {side}")
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


def r_multiple(entry_price: float, exit_price: float, stop_price: float, side: str) -> float:
    if side == "long":
        risk = entry_price - stop_price
        result = exit_price - entry_price
    elif side == "short":
        risk = stop_price - entry_price
        result = entry_price - exit_price
    else:
        raise ValueError(f"Unsupported side: {side}")
    if risk <= 0:
        return 0.0
    return round(result / risk, 2)


def create_trade_plan(
    signal: dict,
    entry_price: float,
    stop_price: float,
    target_price: float,
    account_equity: float | None = None,
    setup_grade: str | None = None,
    notes: str = "",
) -> dict:
    """Create a complete trade plan from a stored signal."""
    side = str(signal.get("side", "long")).lower()
    rr = reward_to_risk(entry_price, stop_price, target_price, side)
    plan = {
        "id": str(uuid.uuid4()),
        "created_at": _utc_now(),
        "signal_id": signal.get("id"),
        "symbol": str(signal["symbol"]).upper(),
        "side": side,
        "setup": signal["setup"],
        "setup_grade": setup_grade,
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "target_price": float(target_price),
        "reward_to_risk": rr,
        "account_equity": float(account_equity) if account_equity is not None else None,
        "source": signal.get("source"),
        "timeframe": signal.get("timeframe"),
        "trigger": signal.get("trigger"),
        "notes": notes or signal.get("notes", ""),
    }
    return plan


def evaluate_trade_plan(plan: dict, today_trades: list[dict] | None = None) -> dict:
    """
    Return an approval decision without placing an order.

    Decision shape:
      approved: bool
      grade: A/B/C/Reject
      reasons: list[str]
      warnings: list[str]
      sized_risk_dollars: float | None
    """
    policy = load_trading_policy()
    rules = load_risk_rules()
    setups = approved_setup_ids()
    today_trades = today_trades if today_trades is not None else trades_for_date(str(date.today()))

    reasons: list[str] = []
    warnings: list[str] = []

    if policy.get("allow_live_trading"):
        warnings.append("Policy allows live trading; current roadmap expects paper-first.")
    if not policy.get("coach_mode", True):
        warnings.append("Coach mode is disabled.")
    if policy.get("require_setup_tag", True) and not plan.get("setup"):
        reasons.append("Missing setup tag.")
    if plan.get("setup") not in setups:
        reasons.append(f"Setup is not approved in playbook: {plan.get('setup')}")
    if policy.get("require_stop_before_entry", True) and not plan.get("stop_price"):
        reasons.append("Missing planned stop.")
    if policy.get("require_target_before_entry", True) and not plan.get("target_price"):
        reasons.append("Missing planned target.")

    min_rr = float(rules.get("minimum_reward_to_risk", 2.0))
    if float(plan.get("reward_to_risk") or 0) < min_rr:
        reasons.append(
            f"Reward/risk {plan.get('reward_to_risk')} is below required {min_rr}."
        )

    closed_losses = [
        t for t in today_trades
        if t.get("outcome") == "loss" and t.get("closed_at")
    ]
    if rules.get("block_after_max_trades", True):
        max_trades = int(rules.get("max_trades_per_day", 999))
        if len(today_trades) >= max_trades:
            reasons.append(f"Daily max trades reached: {len(today_trades)}/{max_trades}.")

    if rules.get("block_after_loss_streak", True):
        max_losses = int(rules.get("max_consecutive_losses", 999))
        if len(closed_losses) >= max_losses:
            reasons.append(f"Max daily loss streak reached: {len(closed_losses)}/{max_losses}.")

    account_equity = plan.get("account_equity")
    sized_risk = None
    if account_equity:
        sized_risk = round(float(account_equity) * float(rules.get("risk_per_trade_pct", 0.0025)), 2)
    else:
        warnings.append("No account equity supplied; cannot calculate dollar risk.")

    approved = not reasons
    grade = "Reject"
    if approved:
        supplied_grade = (plan.get("setup_grade") or "").upper()
        grade = supplied_grade if supplied_grade in {"A", "B", "C"} else "B"

    return {
        "approved": approved,
        "grade": grade,
        "reasons": reasons,
        "warnings": warnings,
        "sized_risk_dollars": sized_risk,
        "evaluated_at": _utc_now(),
    }


def journal_trade_plan(plan: dict, decision: dict) -> dict:
    """Persist a planned trade with its coach decision."""
    entry = {
        "id": str(uuid.uuid4()),
        "date": str(date.today()),
        "created_at": _utc_now(),
        "status": "planned" if decision["approved"] else "rejected",
        "outcome": "planned" if decision["approved"] else "cancelled",
        "coach_decision": decision,
        "plan": plan,
        "fills": [],
        "exit": None,
        "pnl": None,
        "pnl_pct": None,
        "r_multiple": None,
        "mistake_tags": [],
        "review_notes": "",
    }
    journal = _load_json(TRADE_JOURNAL_FILE, [])
    journal.append(entry)
    _save_json(TRADE_JOURNAL_FILE, journal)

    signal_id = plan.get("signal_id")
    if signal_id:
        update_signal_status(signal_id, "planned" if decision["approved"] else "rejected")
    return entry


def record_trade_open(journal_id: str, broker_order_id: str | None = None, fills: list[dict] | None = None) -> dict:
    journal = _load_json(TRADE_JOURNAL_FILE, [])
    for entry in journal:
        if entry["id"] == journal_id:
            entry["status"] = "open"
            entry["outcome"] = "open"
            entry["opened_at"] = _utc_now()
            entry["broker_order_id"] = broker_order_id
            entry["fills"] = fills or []
            _save_json(TRADE_JOURNAL_FILE, journal)
            return entry
    raise ValueError(f"Journal entry not found: {journal_id}")


def record_trade_exit(
    journal_id: str,
    exit_price: float,
    exit_reason: str,
    mistake_tags: list[str] | None = None,
    review_notes: str = "",
) -> dict:
    journal = _load_json(TRADE_JOURNAL_FILE, [])
    for entry in journal:
        if entry["id"] != journal_id:
            continue
        plan = entry["plan"]
        side = plan["side"]
        entry_price = float(plan["entry_price"])
        stop_price = float(plan["stop_price"])
        result_r = r_multiple(entry_price, float(exit_price), stop_price, side)
        pnl_pct = (
            (float(exit_price) - entry_price) / entry_price * 100
            if side == "long"
            else (entry_price - float(exit_price)) / entry_price * 100
        )
        entry.update({
            "status": "closed",
            "outcome": "win" if result_r > 0 else "loss" if result_r < 0 else "breakeven",
            "closed_at": _utc_now(),
            "exit": {
                "price": float(exit_price),
                "reason": exit_reason,
            },
            "pnl_pct": round(pnl_pct, 2),
            "r_multiple": result_r,
            "mistake_tags": mistake_tags or [],
            "review_notes": review_notes,
        })
        _save_json(TRADE_JOURNAL_FILE, journal)
        return entry
    raise ValueError(f"Journal entry not found: {journal_id}")


def trades_for_date(day: str) -> list[dict]:
    journal = _load_json(TRADE_JOURNAL_FILE, [])
    return [entry for entry in journal if entry.get("date") == day]


def build_daily_review(day: str | None = None) -> dict:
    day = day or str(date.today())
    trades = trades_for_date(day)
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = [t for t in closed if t.get("outcome") == "win"]
    losses = [t for t in closed if t.get("outcome") == "loss"]
    total_r = round(sum(float(t.get("r_multiple") or 0) for t in closed), 2)
    mistake_counts: dict[str, int] = {}
    for trade in closed:
        for tag in trade.get("mistake_tags", []):
            mistake_counts[tag] = mistake_counts.get(tag, 0) + 1

    review = {
        "date": day,
        "created_at": _utc_now(),
        "trades": len(trades),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 2) if closed else None,
        "total_r": total_r,
        "average_r": round(total_r / len(closed), 2) if closed else None,
        "mistake_counts": mistake_counts,
        "coach_focus": _coach_focus(trades, closed, mistake_counts),
    }

    reviews = _load_json(DAILY_REVIEWS_FILE, [])
    reviews = [r for r in reviews if r.get("date") != day]
    reviews.append(review)
    _save_json(DAILY_REVIEWS_FILE, reviews)
    return review


def _coach_focus(trades: list[dict], closed: list[dict], mistake_counts: dict[str, int]) -> str:
    if not trades:
        return "No trades logged. Focus on waiting for only approved setups."
    rejected = [t for t in trades if t.get("status") == "rejected"]
    if rejected:
        return "Review rejected plans and tighten pre-trade selection."
    if mistake_counts:
        worst = max(mistake_counts, key=mistake_counts.get)
        return f"Primary behavior focus: reduce '{worst}' mistakes."
    if closed:
        return "Review screenshots and notes for the best and worst executed trades."
    return "Open/planned trades only. Keep stops fixed and avoid adding risk."


def validate_foundation_files() -> dict:
    """Quick health check for policy, risk rules, playbook, inbox, and journal files."""
    policy = load_trading_policy()
    rules = load_risk_rules()
    playbook = load_playbook()
    problems = []

    if policy.get("allow_live_trading"):
        problems.append("trading_policy.json allows live trading.")
    if not policy.get("coach_mode", True):
        problems.append("trading_policy.json has coach_mode disabled.")
    if not playbook.get("approved_setups"):
        problems.append("playbook.json has no approved setups.")
    if float(rules.get("risk_per_trade_pct", 0)) <= 0:
        problems.append("risk_rules.json risk_per_trade_pct must be positive.")
    if float(rules.get("minimum_reward_to_risk", 0)) < 1:
        problems.append("risk_rules.json minimum_reward_to_risk is too low.")

    return {
        "ok": not problems,
        "problems": problems,
        "approved_setups": sorted(approved_setup_ids(playbook)),
    }


if __name__ == "__main__":
    health = validate_foundation_files()
    print(json.dumps(health, indent=2))
