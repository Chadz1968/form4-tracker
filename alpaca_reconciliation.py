"""
alpaca_reconciliation.py

Alpaca paper-account reconciliation for the day trading manager coach.

This module imports broker state into local JSON snapshots and updates
trade_journal.json from Alpaca order fills. It is intentionally conservative:
it only updates journal entries that already carry a broker_order_id, so broker
execution cannot create mysterious trades without a prior plan.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import trade_manager_journal as journal


_DIR = os.path.dirname(os.path.abspath(__file__))
ALPACA_ACCOUNT_SNAPSHOT_FILE = os.path.join(_DIR, "alpaca_account_snapshot.json")
ALPACA_POSITIONS_SNAPSHOT_FILE = os.path.join(_DIR, "alpaca_positions_snapshot.json")
ALPACA_ORDERS_SNAPSHOT_FILE = os.path.join(_DIR, "alpaca_orders_snapshot.json")
ALPACA_RECONCILIATION_LOG_FILE = os.path.join(_DIR, "alpaca_reconciliation_log.json")

OPEN_ORDER_STATUSES = {"new", "accepted", "pending_new", "partially_filled"}
FILLED_STATUS = "filled"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _enum_value(value: Any) -> str | None:
    raw = _get(value, "value", value)
    if raw is None:
        return None
    return str(raw).lower()


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def normalize_account(account: Any) -> dict:
    return {
        "id": _as_str(_get(account, "id")),
        "status": _as_str(_get(account, "status")),
        "currency": _as_str(_get(account, "currency")),
        "cash": _as_float(_get(account, "cash")),
        "equity": _as_float(_get(account, "equity")),
        "last_equity": _as_float(_get(account, "last_equity")),
        "buying_power": _as_float(_get(account, "buying_power")),
        "daytrade_count": _get(account, "daytrade_count"),
        "pattern_day_trader": bool(_get(account, "pattern_day_trader", False)),
        "snapshot_at": _utc_now(),
    }


def normalize_position(position: Any) -> dict:
    return {
        "asset_id": _as_str(_get(position, "asset_id")),
        "symbol": _as_str(_get(position, "symbol")),
        "side": _enum_value(_get(position, "side")),
        "qty": _as_float(_get(position, "qty")),
        "avg_entry_price": _as_float(_get(position, "avg_entry_price")),
        "market_value": _as_float(_get(position, "market_value")),
        "cost_basis": _as_float(_get(position, "cost_basis")),
        "unrealized_pl": _as_float(_get(position, "unrealized_pl")),
        "unrealized_plpc": _as_float(_get(position, "unrealized_plpc")),
        "current_price": _as_float(_get(position, "current_price")),
    }


def normalize_order(order: Any) -> dict:
    legs = [_normalize_order_leg(leg) for leg in (_get(order, "legs", None) or [])]
    return {
        "id": _as_str(_get(order, "id")),
        "client_order_id": _as_str(_get(order, "client_order_id")),
        "symbol": _as_str(_get(order, "symbol")),
        "side": _enum_value(_get(order, "side")),
        "order_type": _enum_value(_get(order, "order_type")),
        "order_class": _enum_value(_get(order, "order_class")),
        "status": _enum_value(_get(order, "status")),
        "qty": _as_float(_get(order, "qty")),
        "filled_qty": _as_float(_get(order, "filled_qty")),
        "filled_avg_price": _as_float(_get(order, "filled_avg_price")),
        "submitted_at": _as_str(_get(order, "submitted_at")),
        "filled_at": _as_str(_get(order, "filled_at")),
        "canceled_at": _as_str(_get(order, "canceled_at")),
        "expired_at": _as_str(_get(order, "expired_at")),
        "legs": legs,
    }


def _normalize_order_leg(leg: Any) -> dict:
    return {
        "id": _as_str(_get(leg, "id")),
        "symbol": _as_str(_get(leg, "symbol")),
        "side": _enum_value(_get(leg, "side")),
        "order_type": _enum_value(_get(leg, "order_type")),
        "status": _enum_value(_get(leg, "status")),
        "qty": _as_float(_get(leg, "qty")),
        "filled_qty": _as_float(_get(leg, "filled_qty")),
        "filled_avg_price": _as_float(_get(leg, "filled_avg_price")),
        "filled_at": _as_str(_get(leg, "filled_at")),
    }


def fetch_alpaca_state(client: Any | None = None) -> dict:
    """Fetch and normalize account, positions, and recent orders."""
    if client is None:
        from config import get_trading_client

        client = get_trading_client()
    account = normalize_account(client.get_account())
    positions = [normalize_position(p) for p in client.get_all_positions()]
    orders = [normalize_order(o) for o in _fetch_recent_orders(client)]
    return {
        "account": account,
        "positions": positions,
        "orders": orders,
        "snapshot_at": _utc_now(),
    }


def _fetch_recent_orders(client: Any) -> list[Any]:
    """
    Fetch recent orders. Fake test clients may expose get_orders() without args;
    real Alpaca clients use GetOrdersRequest.
    """
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        return client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        )
    except ModuleNotFoundError:
        return client.get_orders()
    except TypeError:
        return client.get_orders()


def save_alpaca_snapshots(state: dict) -> None:
    _save_json(ALPACA_ACCOUNT_SNAPSHOT_FILE, state["account"])
    _save_json(ALPACA_POSITIONS_SNAPSHOT_FILE, state["positions"])
    _save_json(ALPACA_ORDERS_SNAPSHOT_FILE, state["orders"])


def reconcile_from_alpaca(client: Any | None = None) -> dict:
    """
    Fetch Alpaca state, save snapshots, and reconcile known journal entries.

    Returns a compact report with counts and journal entry ids updated.
    """
    state = fetch_alpaca_state(client)
    save_alpaca_snapshots(state)

    orders_by_id = {o["id"]: o for o in state["orders"] if o.get("id")}
    journal_entries = _load_json(journal.TRADE_JOURNAL_FILE, [])

    opened: list[str] = []
    closed: list[str] = []
    ignored: list[str] = []

    for entry in journal_entries:
        order_id = entry.get("broker_order_id")
        if not order_id:
            ignored.append(entry["id"])
            continue
        order = orders_by_id.get(order_id)
        if not order:
            ignored.append(entry["id"])
            continue

        if entry.get("status") in {"planned", "rejected"} and _order_has_entry_fill(order):
            _mark_open_from_order(entry, order)
            opened.append(entry["id"])

        if entry.get("status") == "open":
            exit_fill = _find_exit_fill(order)
            if exit_fill:
                _mark_closed_from_exit(entry, exit_fill)
                closed.append(entry["id"])

    _save_json(journal.TRADE_JOURNAL_FILE, journal_entries)
    report = {
        "reconciled_at": _utc_now(),
        "account_equity": state["account"].get("equity"),
        "positions": len(state["positions"]),
        "orders": len(state["orders"]),
        "opened": opened,
        "closed": closed,
        "ignored": ignored,
    }
    _append_reconciliation_log(report)
    return report


def _order_has_entry_fill(order: dict) -> bool:
    status = order.get("status")
    filled_qty = order.get("filled_qty") or 0
    return status in {FILLED_STATUS, "partially_filled"} and filled_qty > 0


def _mark_open_from_order(entry: dict, order: dict) -> None:
    fill = {
        "broker": "alpaca",
        "order_id": order["id"],
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "qty": order.get("filled_qty"),
        "price": order.get("filled_avg_price"),
        "filled_at": order.get("filled_at"),
        "role": "entry",
    }
    fills = entry.get("fills") or []
    if not any(f.get("order_id") == fill["order_id"] and f.get("role") == "entry" for f in fills):
        fills.append(fill)
    entry.update({
        "status": "open",
        "outcome": "open",
        "opened_at": order.get("filled_at") or _utc_now(),
        "fills": fills,
    })


def _find_exit_fill(order: dict) -> dict | None:
    for leg in order.get("legs", []):
        if leg.get("status") == FILLED_STATUS and leg.get("filled_avg_price"):
            reason = "stop" if "stop" in str(leg.get("order_type")) else "target"
            return {
                "broker": "alpaca",
                "order_id": leg.get("id"),
                "parent_order_id": order.get("id"),
                "symbol": leg.get("symbol") or order.get("symbol"),
                "side": leg.get("side"),
                "qty": leg.get("filled_qty"),
                "price": leg.get("filled_avg_price"),
                "filled_at": leg.get("filled_at"),
                "reason": reason,
                "role": "exit",
            }
    return None


def _mark_closed_from_exit(entry: dict, exit_fill: dict) -> None:
    plan = entry["plan"]
    side = plan["side"]
    entry_price = float(plan["entry_price"])
    stop_price = float(plan["stop_price"])
    exit_price = float(exit_fill["price"])
    result_r = journal.r_multiple(entry_price, exit_price, stop_price, side)

    pnl_pct = (
        (exit_price - entry_price) / entry_price * 100
        if side == "long"
        else (entry_price - exit_price) / entry_price * 100
    )
    qty = _infer_quantity(entry)
    pnl = round((exit_price - entry_price) * qty, 2) if side == "long" and qty else None
    if side == "short" and qty:
        pnl = round((entry_price - exit_price) * qty, 2)

    fills = entry.get("fills") or []
    if not any(f.get("order_id") == exit_fill["order_id"] and f.get("role") == "exit" for f in fills):
        fills.append(exit_fill)

    entry.update({
        "status": "closed",
        "outcome": "win" if result_r > 0 else "loss" if result_r < 0 else "breakeven",
        "closed_at": exit_fill.get("filled_at") or _utc_now(),
        "exit": {
            "price": exit_price,
            "reason": exit_fill["reason"],
            "broker_order_id": exit_fill.get("order_id"),
        },
        "fills": fills,
        "pnl": pnl,
        "pnl_pct": round(pnl_pct, 2),
        "r_multiple": result_r,
    })


def _infer_quantity(entry: dict) -> float | None:
    fills = entry.get("fills") or []
    entry_fills = [f for f in fills if f.get("role") == "entry" and f.get("qty")]
    if entry_fills:
        return sum(float(f["qty"]) for f in entry_fills)
    qty = entry.get("plan", {}).get("qty") or entry.get("plan", {}).get("shares")
    return _as_float(qty)


def _append_reconciliation_log(report: dict) -> None:
    log = _load_json(ALPACA_RECONCILIATION_LOG_FILE, [])
    log.append(report)
    _save_json(ALPACA_RECONCILIATION_LOG_FILE, log)


if __name__ == "__main__":
    result = reconcile_from_alpaca()
    print(json.dumps(result, indent=2, default=str))
