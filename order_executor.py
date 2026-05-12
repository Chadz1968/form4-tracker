"""
order_executor.py

Position sizing and Alpaca bracket order submission for the day trading
manager coach.

Called only after trade_manager_journal.evaluate_trade_plan() has returned
approved=True.  Never submits a rejected or unreviewed plan.
"""

from __future__ import annotations

import math
from typing import Any

import trade_manager_journal as journal


def calculate_shares(
    entry_price: float,
    stop_price: float,
    account_equity: float,
    rules: dict | None = None,
) -> int:
    """
    Return the number of whole shares that risks at most risk_per_trade_pct
    of account equity, capped by max_position_notional_pct.

    Returns 0 if the stop distance is zero or sizing is too small.
    """
    rules = rules or journal.load_risk_rules()
    risk_dollars = account_equity * float(rules.get("risk_per_trade_pct", 0.0025))
    risk_per_share = abs(float(entry_price) - float(stop_price))
    if risk_per_share <= 0:
        return 0

    shares_by_risk = math.floor(risk_dollars / risk_per_share)

    max_notional = account_equity * float(rules.get("max_position_notional_pct", 0.10))
    shares_by_notional = math.floor(max_notional / float(entry_price))

    return max(0, min(shares_by_risk, shares_by_notional))


def submit_bracket_order(plan: dict, shares: int, client: Any) -> Any:
    """
    Submit a market-entry bracket order with stop-loss and take-profit legs.

    The entry leg uses TimeInForce.DAY so it cancels if unfilled by close.
    Alpaca automatically makes the child legs GTC.

    Returns the Alpaca Order object.
    """
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import (
        MarketOrderRequest,
        StopLossRequest,
        TakeProfitRequest,
    )

    side = str(plan["side"]).lower()
    order_side = OrderSide.BUY if side == "long" else OrderSide.SELL

    stop_price = round(float(plan["stop_price"]), 2)
    target_price = round(float(plan["target_price"]), 2)

    order_req = MarketOrderRequest(
        symbol=str(plan["symbol"]).upper(),
        qty=shares,
        side=order_side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        stop_loss=StopLossRequest(stop_price=stop_price),
        take_profit=TakeProfitRequest(limit_price=target_price),
    )
    return client.submit_order(order_req)


def execute_approved_plan(plan: dict, decision: dict) -> dict:
    """
    Size the position, submit the bracket order, and journal the open trade.

    Returns a summary dict with order details, shares, and journal entry id.
    Raises ValueError for any pre-submission guard failures.
    """
    if not decision.get("approved"):
        reasons = "; ".join(decision.get("reasons", ["unknown"]))
        raise ValueError(f"Plan not approved by coach: {reasons}")

    account_equity = plan.get("account_equity")
    if not account_equity:
        raise ValueError(
            "Account equity is required for position sizing. "
            "Run Alpaca reconciliation first or enter equity manually."
        )

    rules = journal.load_risk_rules()
    shares = calculate_shares(
        plan["entry_price"], plan["stop_price"], float(account_equity), rules
    )
    if shares < 1:
        raise ValueError(
            f"Position sizes to 0 shares at current equity "
            f"(${account_equity:,.0f}) and stop distance. "
            "Widen the stop or increase account size."
        )

    from config import get_trading_client
    client = get_trading_client()
    order = submit_bracket_order(plan, shares, client)

    journal_entry = journal.journal_trade_plan(plan, decision)
    journal.record_trade_open(
        journal_entry["id"],
        broker_order_id=str(order.id),
        fills=[],
    )

    return {
        "order_id": str(order.id),
        "symbol": plan["symbol"],
        "side": plan["side"],
        "shares": shares,
        "entry": plan["entry_price"],
        "stop": plan["stop_price"],
        "target": plan["target_price"],
        "risk_dollars": round(
            shares * abs(float(plan["entry_price"]) - float(plan["stop_price"])), 2
        ),
        "journal_entry_id": journal_entry["id"],
        "order_status": str(order.status),
    }
