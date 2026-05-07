"""
risk_agent.py

Position sizing and drawdown management for insider-buy signals.

Differences from the gap-fade predecessor:
  - Always long (insider buys only — no short leg)
  - Entry: current market price as next-open estimate (not today_open)
  - Stop: STOP_PCT (12%) below entry for multi-week holds
  - Target: 2:1 reward-to-risk = 24% above entry
  - Filters signals below MIN_LLM_SCORE before any sizing
  - Bracket order child legs persist GTC until one fills
"""

import math
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from alpaca.trading.enums import OrderSide, OrderClass, TimeInForce

from config import (
    get_trading_client,
    RISK_PER_TRADE,
    MAX_DRAWDOWN,
    STOP_PCT,
    MIN_LLM_SCORE,
)

TARGET_MULT = 2.0   # take-profit at 2:1 reward-to-risk


def _get_account(client: TradingClient) -> dict:
    account = client.get_account()
    return {
        "equity":      float(account.equity),
        "last_equity": float(account.last_equity),
    }


def _drawdown_ok(account: dict) -> tuple[bool, float]:
    equity = account["equity"]
    peak   = max(equity, account["last_equity"])
    dd     = (peak - equity) / peak if peak > 0 else 0.0
    return dd < MAX_DRAWDOWN, round(dd, 4)


def _size_position(equity: float, entry: float) -> tuple[int, float, float]:
    """
    Returns (shares, stop_price, target_price).

    Dollar risk  = equity * RISK_PER_TRADE
    Stop dist    = entry  * STOP_PCT          (12% below entry)
    Shares       = floor(dollar_risk / stop_dist)
    Target       = entry  * (1 + STOP_PCT * TARGET_MULT)   (24% above entry)
    """
    dollar_risk  = equity * RISK_PER_TRADE
    stop_dist    = entry  * STOP_PCT
    shares       = max(math.floor(dollar_risk / stop_dist), 1)
    stop_price   = round(entry * (1 - STOP_PCT), 2)
    target_price = round(entry * (1 + STOP_PCT * TARGET_MULT), 2)
    return shares, stop_price, target_price


def evaluate(candidates: list[dict]) -> list[dict]:
    """
    Filter candidates by LLM score, check account drawdown, then calculate
    position sizes for all approved signals.

    Args:
        candidates: output of llm_filter_agent.score_candidates()

    Returns:
        List of approved trade dicts with sizing fields added:
        entry_price, stop_price, target_price, shares, dollar_risk,
        account_equity. Empty list if drawdown limit is hit or no candidates
        clear the score threshold.
    """
    print("[Risk] Evaluating candidates...")

    qualified = [c for c in candidates if (c.get("llm_score") or 0) >= MIN_LLM_SCORE]
    if not qualified:
        print(f"[Risk] No signals above {MIN_LLM_SCORE}/10 — nothing to trade.")
        return []
    print(f"[Risk] {len(qualified)}/{len(candidates)} signals above "
          f"{MIN_LLM_SCORE}/10 threshold.")

    client  = get_trading_client()
    account = _get_account(client)
    equity  = account["equity"]

    dd_ok, drawdown = _drawdown_ok(account)
    print(f"[Risk] Equity=${equity:,.2f} | "
          f"Drawdown={drawdown*100:.2f}% (limit {MAX_DRAWDOWN*100:.0f}%)")

    if not dd_ok:
        print(f"[Risk] HARD STOP — drawdown {drawdown*100:.2f}% exceeds "
              f"{MAX_DRAWDOWN*100:.0f}%. No trades today.")
        return []

    approved = []
    for signal in qualified:
        ticker = signal["ticker"]

        # Prefer live market price; fall back to insider's avg price if unavailable
        entry = (
            (signal.get("price_context") or {}).get("current_price")
            or signal.get("avg_price")
        )
        if not entry:
            print(f"[Risk]   {ticker} — no entry price, skipping.")
            continue

        shares, stop_price, target_price = _size_position(equity, entry)
        dollar_risk = round(shares * (entry - stop_price), 2)

        approved.append({
            **signal,
            "entry_price":    round(entry, 2),
            "stop_price":     stop_price,
            "target_price":   target_price,
            "shares":         shares,
            "dollar_risk":    dollar_risk,
            "account_equity": equity,
        })
        cluster_tag = f" [CLUSTER x{signal['cluster_size']}]" if signal.get("cluster_size", 1) > 1 else ""
        print(
            f"[Risk]   {ticker:6s}{cluster_tag} [{signal['llm_score']:.1f}/10] "
            f"LONG {shares} sh @ ~${entry:.2f} | "
            f"stop ${stop_price} | target ${target_price} | risk ${dollar_risk:,.2f}"
        )

    return approved


def place_order(trade: dict) -> dict:
    """
    Submit a bracket order to Alpaca paper trading.

    Entry:     market order, fills at next open (TimeInForce.DAY)
    Stop-loss: persists GTC until filled or target hits
    Take-profit: persists GTC until filled or stop hits

    Returns minimal dict with order ID and target price for reflector logging.
    """
    client = get_trading_client()

    order_request = MarketOrderRequest(
        symbol        = trade["ticker"],
        qty           = trade["shares"],
        side          = OrderSide.BUY,
        time_in_force = TimeInForce.DAY,
        order_class   = OrderClass.BRACKET,
        stop_loss     = StopLossRequest(stop_price=trade["stop_price"]),
        take_profit   = TakeProfitRequest(limit_price=trade["target_price"]),
    )
    order = client.submit_order(order_request)
    print(
        f"[Risk] Order {order.id} — LONG {trade['shares']} {trade['ticker']} "
        f"| stop=${trade['stop_price']}  target=${trade['target_price']}"
    )
    return {"id": str(order.id), "target_price": trade["target_price"]}
