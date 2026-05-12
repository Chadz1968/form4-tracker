"""
trade_manager_ui.py

Local browser UI for the day trading manager coach.

Run:
  python trade_manager_ui.py --host 127.0.0.1 --port 8787

Then open:
  http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import trade_manager_journal as journal


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY_BYTES = 64_000
_DIR = os.path.dirname(os.path.abspath(__file__))
ALPACA_ACCOUNT_SNAPSHOT_FILE = os.path.join(_DIR, "alpaca_account_snapshot.json")


def _log(message: str) -> None:
    try:
        if sys.stdout:
            print(message)
    except OSError:
        pass


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_signal(signal_id: str) -> dict:
    inbox = _load_json(journal.SIGNAL_INBOX_FILE, [])
    for signal in inbox:
        if signal.get("id") == signal_id:
            return signal
    raise ValueError(f"Signal not found: {signal_id}")


def _latest_account_equity() -> float | None:
    snapshot = _load_json(ALPACA_ACCOUNT_SNAPSHOT_FILE, {})
    equity = snapshot.get("equity")
    if equity in (None, ""):
        return None
    return float(equity)


def load_ui_state() -> dict:
    signals = _load_json(journal.SIGNAL_INBOX_FILE, [])
    trades = _load_json(journal.TRADE_JOURNAL_FILE, [])
    reviews = _load_json(journal.DAILY_REVIEWS_FILE, [])
    return {
        "signals": sorted(signals, key=lambda s: s.get("created_at", ""), reverse=True),
        "trades": sorted(trades, key=lambda t: t.get("created_at", ""), reverse=True),
        "reviews": sorted(reviews, key=lambda r: r.get("created_at", ""), reverse=True),
        "playbook": journal.load_playbook(),
        "risk_rules": journal.load_risk_rules(),
        "trading_policy": journal.load_trading_policy(),
        "account_equity": _latest_account_equity(),
    }


def build_plan_from_payload(payload: dict) -> dict:
    signal = _find_signal(payload.get("signal_id", ""))
    account_equity = payload.get("account_equity")
    if account_equity in (None, ""):
        account_equity = _latest_account_equity()

    def _require_float(val, name):
        if val in (None, ""):
            raise ValueError(f"{name} is required")
        return float(val)

    return journal.create_trade_plan(
        signal,
        entry_price=_require_float(payload.get("entry_price"), "Entry"),
        stop_price=_require_float(payload.get("stop_price"), "Stop"),
        target_price=_require_float(payload.get("target_price"), "Target"),
        account_equity=float(account_equity) if account_equity not in (None, "") else None,
        setup_grade=payload.get("setup_grade") or None,
        notes=payload.get("notes", ""),
    )


def suggest_levels(payload: dict) -> dict:
    signal = _find_signal(payload.get("signal_id", ""))
    entry = float(signal.get("price") or 0)
    setup = signal.get("setup", "")
    levels = signal.get("levels") or {}
    vwap = levels.get("vwap")
    _BUFFER = 0.003  # 0.3% below key level for stop

    stop = None
    if setup in ("vwap_reclaim", "vwap_pullback") and vwap:
        stop = round(vwap * (1 - _BUFFER), 2)
    elif setup == "opening_range_breakout" and levels.get("or_high"):
        stop = round(levels["or_high"] * (1 - _BUFFER), 2)
    elif setup == "failed_breakdown_reversal" and levels.get("support"):
        stop = round(levels["support"] * (1 - _BUFFER), 2)
    elif setup == "news_momentum" and vwap:
        stop = round(vwap * (1 - _BUFFER), 2)

    target = None
    if stop and entry and entry > stop:
        risk = entry - stop
        target = round(entry + 2 * risk, 2)

    return {"stop": stop, "target": target, "entry": entry, "levels": levels}


def coach_check(payload: dict) -> dict:
    plan = build_plan_from_payload(payload)
    decision = journal.evaluate_trade_plan(plan)
    return {"plan": plan, "decision": decision}


def save_trade_plan(payload: dict) -> dict:
    plan = build_plan_from_payload(payload)
    decision = journal.evaluate_trade_plan(plan)
    entry = journal.journal_trade_plan(plan, decision)
    return {"plan": plan, "decision": decision, "journal_entry": entry}


def update_signal(payload: dict) -> dict:
    signal_id = payload.get("signal_id")
    status = payload.get("status")
    if not signal_id or not status:
        raise ValueError("signal_id and status are required.")
    journal.update_signal_status(signal_id, status)
    return {"ok": True, "signal_id": signal_id, "status": status}


def get_daily_review(payload: dict) -> dict:
    day = payload.get("date") or str(__import__("datetime").date.today())
    review = journal.build_daily_review(day)
    return {"review": review}


def add_signal(payload: dict) -> dict:
    symbol = str(payload.get("symbol") or "").upper().strip()
    price = payload.get("price")
    setup = str(payload.get("setup") or "").strip()
    if not symbol or not price or not setup:
        raise ValueError("symbol, price, and setup are required.")
    signal = journal.ingest_signal({
        "source": "manual",
        "symbol": symbol,
        "timeframe": str(payload.get("timeframe") or "5m"),
        "setup": setup,
        "side": "long",
        "price": float(price),
        "trigger": "manual_entry",
        "notes": str(payload.get("notes") or ""),
    })
    return {"signal": signal}


def submit_order(payload: dict) -> dict:
    import order_executor
    plan = build_plan_from_payload(payload)
    decision = journal.evaluate_trade_plan(plan)
    result = order_executor.execute_approved_plan(plan, decision)
    return {"plan": plan, "decision": decision, "order": result}


def handle_api(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    payload = payload or {}
    try:
        if method == "GET" and path == "/api/state":
            return 200, {"ok": True, **load_ui_state()}
        if method == "POST" and path == "/api/suggest-levels":
            return 200, {"ok": True, **suggest_levels(payload)}
        if method == "POST" and path == "/api/coach-check":
            return 200, {"ok": True, **coach_check(payload)}
        if method == "POST" and path == "/api/save-plan":
            return 201, {"ok": True, **save_trade_plan(payload)}
        if method == "POST" and path == "/api/submit-order":
            return 201, {"ok": True, **submit_order(payload)}
        if method == "POST" and path == "/api/add-signal":
            return 201, {"ok": True, **add_signal(payload)}
        if method == "POST" and path == "/api/daily-review":
            return 200, {"ok": True, **get_daily_review(payload)}
        if method == "POST" and path == "/api/signal-status":
            return 200, update_signal(payload)
    except (KeyError, TypeError, ValueError) as exc:
        return 400, {"ok": False, "error": str(exc)}
    return 404, {"ok": False, "error": "Not found."}


class TradeManagerUiHandler(BaseHTTPRequestHandler):
    server_version = "TradeManagerUI/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(INDEX_HTML)
            return
        if path == "/health":
            self._send_json(200, {"ok": True, "service": "trade_manager_ui"})
            return
        status, response = handle_api("GET", path)
        self._send_json(status, response)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "Request body too large."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Body must be valid JSON."})
            return

        status, response = handle_api("POST", path, payload)
        self._send_json(status, response)

    def log_message(self, fmt: str, *args: Any) -> None:
        _log(f"[TradeManagerUI] {self.address_string()} - {fmt % args}")

    def log_error(self, fmt: str, *args: Any) -> None:
        _log(f"[TradeManagerUI][ERROR] {self.address_string()} - {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trade Manager Coach</title>
  <style>
    :root {
      --bg: #f5f6f8;
      --surface: #ffffff;
      --line: #d9dde5;
      --text: #1f2633;
      --muted: #657084;
      --accent: #146c63;
      --accent-dark: #0f514b;
      --danger: #a43737;
      --warn: #8b6514;
      --good: #17633b;
      --field: #fbfcfe;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    header {
      min-height: 58px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
    }
    h2 {
      margin: 0 0 10px;
      font-size: 14px;
      font-weight: 700;
    }
    button, select, input, textarea {
      font: inherit;
    }
    button {
      min-height: 34px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      border-radius: 6px;
      padding: 6px 10px;
      cursor: pointer;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }
    button.primary:hover { background: var(--accent-dark); }
    button:disabled {
      color: var(--muted);
      background: #eef0f4;
      cursor: not-allowed;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(280px, 380px) minmax(360px, 1fr);
      gap: 14px;
      padding: 14px;
      min-height: calc(100vh - 58px);
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    .panel-body { padding: 12px; }
    .signal-list {
      display: grid;
      gap: 8px;
      max-height: calc(100vh - 150px);
      overflow: auto;
    }
    .signal-row {
      width: 100%;
      text-align: left;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 6px;
      padding: 10px;
      border: 1px solid var(--line);
      background: var(--field);
      border-radius: 6px;
    }
    .signal-row.active {
      border-color: var(--accent);
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .symbol { font-weight: 700; font-size: 16px; }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      background: #f4f6f9;
      font-size: 12px;
      white-space: nowrap;
    }
    .badge.good { color: var(--good); border-color: #b8d8c7; background: #eef8f2; }
    .badge.bad { color: var(--danger); border-color: #e1b9b9; background: #fff1f1; }
    .main-grid {
      display: grid;
      grid-template-columns: minmax(360px, 1fr) minmax(320px, 0.8fr);
      gap: 14px;
    }
    form {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--field);
      color: var(--text);
      padding: 8px;
      min-height: 36px;
    }
    textarea {
      min-height: 84px;
      resize: vertical;
    }
    .span-2 { grid-column: 1 / -1; }
    .actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    .decision {
      display: grid;
      gap: 10px;
    }
    .decision-box {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: var(--field);
      min-height: 82px;
    }
    .decision-title {
      font-weight: 700;
      margin-bottom: 6px;
    }
    .decision-title.approved { color: var(--good); }
    .decision-title.rejected { color: var(--danger); }
    .journal {
      margin-top: 14px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      background: var(--surface);
    }
    th, td {
      border-top: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
      word-wrap: break-word;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      background: #f7f8fa;
    }
    .empty {
      color: var(--muted);
      padding: 18px;
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 6px;
      background: var(--field);
    }
    .status-line {
      min-height: 18px;
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 900px) {
      .layout, .main-grid { grid-template-columns: 1fr; }
      .signal-list { max-height: 280px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Trade Manager Coach</h1>
    <div class="status-line" id="statusLine">Loading...</div>
    <button id="refreshBtn">Refresh</button>
  </header>
  <main class="layout">
    <section class="panel">
      <div class="panel-head">
        <h2>Signal Inbox</h2>
        <div style="display:flex;gap:6px;align-items:center">
          <select id="signalFilter" title="Filter signal status">
            <option value="">All</option>
            <option value="new">New</option>
            <option value="planned">Planned</option>
            <option value="rejected">Rejected</option>
            <option value="expired">Expired</option>
            <option value="traded">Traded</option>
          </select>
          <button id="addSignalToggle" title="Add a signal you spotted manually">+ Add</button>
        </div>
      </div>
      <div class="panel-body">
        <div id="addSignalForm" style="display:none;margin-bottom:12px;padding:10px;background:var(--field);border:1px solid var(--line);border-radius:6px;">
          <div style="font-weight:700;font-size:12px;color:var(--muted);margin-bottom:8px;">Manual Signal</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <label style="display:grid;gap:4px;font-size:12px;font-weight:700;color:var(--muted);">Symbol
              <input id="manualSymbol" placeholder="NVDA" style="text-transform:uppercase">
            </label>
            <label style="display:grid;gap:4px;font-size:12px;font-weight:700;color:var(--muted);">Price
              <input id="manualPrice" type="number" step="0.01" placeholder="0.00">
            </label>
            <label style="display:grid;gap:4px;font-size:12px;font-weight:700;color:var(--muted);">Setup
              <select id="manualSetup">
                <option value="opening_range_breakout">ORB</option>
                <option value="vwap_reclaim">VWAP Reclaim</option>
                <option value="vwap_pullback">VWAP Pullback</option>
                <option value="news_momentum">News Momentum</option>
                <option value="failed_breakdown_reversal">Failed Breakdown</option>
              </select>
            </label>
            <label style="display:grid;gap:4px;font-size:12px;font-weight:700;color:var(--muted);">Timeframe
              <select id="manualTimeframe">
                <option value="1m">1m</option>
                <option value="5m" selected>5m</option>
                <option value="15m">15m</option>
                <option value="30m">30m</option>
              </select>
            </label>
            <label style="display:grid;gap:4px;font-size:12px;font-weight:700;color:var(--muted);grid-column:1/-1;">Notes
              <input id="manualNotes" placeholder="Why this setup looks good…">
            </label>
          </div>
          <div style="display:flex;gap:8px;margin-top:8px;">
            <button class="primary" id="addSignalSubmit">Add to Inbox</button>
            <button id="addSignalCancel">Cancel</button>
          </div>
        </div>
        <div class="signal-list" id="signalList"></div>
      </div>
    </section>

    <section>
      <div class="main-grid">
        <section class="panel">
          <div class="panel-head">
            <h2>Trade Plan</h2>
            <span class="badge" id="selectedBadge">No signal selected</span>
          </div>
          <div class="panel-body">
            <form id="planForm">
              <label>Symbol
                <input id="symbol" disabled>
              </label>
              <label>Setup
                <input id="setup" disabled>
              </label>
              <label>Side
                <input id="side" disabled>
              </label>
              <label>Timeframe
                <input id="timeframe" disabled>
              </label>
              <label>Entry
                <input id="entryPrice" type="number" step="0.01" required>
              </label>
              <label>Stop
                <input id="stopPrice" type="number" step="0.01" required>
              </label>
              <label>Target
                <input id="targetPrice" type="number" step="0.01" required>
              </label>
              <label>Setup Grade
                <select id="setupGrade">
                  <option value="">Auto</option>
                  <option value="A">A</option>
                  <option value="B">B</option>
                  <option value="C">C</option>
                </select>
              </label>
              <label>Account Equity
                <input id="accountEquity" type="number" step="0.01">
              </label>
              <label>Reward/Risk
                <input id="rewardRisk" disabled>
              </label>
              <label class="span-2">Notes
                <textarea id="notes"></textarea>
              </label>
            </form>
            <div class="actions">
              <button class="primary" id="coachBtn">Coach Check</button>
              <button id="submitBtn" disabled title="Run Coach Check first — only enabled when plan is approved">Submit Order</button>
              <button id="saveBtn">Save Plan</button>
              <button id="expireBtn">Expire Signal</button>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>Coach Decision</h2>
            <span class="badge" id="decisionBadge">Waiting</span>
          </div>
          <div class="panel-body decision">
            <div class="decision-box" id="decisionBox">
              <div class="muted">Select a signal and run Coach Check.</div>
            </div>
            <div class="decision-box">
              <div class="decision-title">Current Rules</div>
              <div id="rulesBox" class="small muted"></div>
            </div>
          </div>
        </section>
      </div>

      <section class="panel journal">
        <div class="panel-head">
          <h2>Journal</h2>
          <span class="badge" id="journalCount">0 trades</span>
        </div>
        <div class="panel-body" id="journalTable"></div>
      </section>

      <section class="panel journal">
        <div class="panel-head">
          <h2>Daily Review</h2>
          <button id="reviewBtn">Build Review</button>
        </div>
        <div class="panel-body" id="reviewBox">
          <div class="muted" style="padding:8px 0">Click Build Review to generate today's coaching summary.</div>
        </div>
      </section>
    </section>
  </main>

  <script>
    const state = { signals: [], trades: [], selected: null, lastDecision: null, accountEquity: null };
    const el = (id) => document.getElementById(id);

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || "Request failed");
      }
      return data;
    }

    async function loadState() {
      const data = await api("/api/state");
      state.signals = data.signals || [];
      state.trades = data.trades || [];
      state.accountEquity = data.account_equity;
      el("statusLine").textContent = `${state.signals.length} signals | ${state.trades.length} journal entries`;
      renderSignals();
      renderRules(data.risk_rules || {}, data.trading_policy || {});
      renderJournal();
    }

    function renderSignals() {
      const filter = el("signalFilter").value;
      const list = el("signalList");
      const signals = state.signals.filter(s => !filter || s.status === filter);
      list.innerHTML = "";
      if (!signals.length) {
        list.innerHTML = `<div class="empty">No signals match this filter.</div>`;
        return;
      }
      for (const signal of signals) {
        const row = document.createElement("button");
        row.className = "signal-row" + (state.selected?.id === signal.id ? " active" : "");
        row.innerHTML = `
          <div>
            <div><span class="symbol">${escapeHtml(signal.symbol)}</span> <span class="muted">${escapeHtml(signal.side || "")}</span></div>
            <div class="small muted">${escapeHtml(signal.setup || "")} | ${escapeHtml(signal.timeframe || "")} | ${escapeHtml(signal.trigger || "")}</div>
            <div class="small muted">$${Number(signal.price || 0).toFixed(2)}</div>
          </div>
          <span class="badge">${escapeHtml(signal.status || "new")}</span>
        `;
        row.addEventListener("click", () => selectSignal(signal));
        list.appendChild(row);
      }
    }

    async function selectSignal(signal) {
      state.selected = signal;
      state.lastDecision = null;
      el("selectedBadge").textContent = signal.status || "new";
      el("symbol").value = signal.symbol || "";
      el("setup").value = signal.setup || "";
      el("side").value = signal.side || "";
      el("timeframe").value = signal.timeframe || "";
      el("entryPrice").value = signal.price || "";
      el("stopPrice").value = "";
      el("targetPrice").value = "";
      el("setupGrade").value = "";
      el("accountEquity").value = state.accountEquity || "";
      el("notes").value = signal.notes || "";
      el("decisionBadge").textContent = "Waiting";
      el("decisionBox").innerHTML = `<div class="muted">Suggesting stop &amp; target…</div>`;
      updateRewardRisk();
      renderSignals();

      try {
        const s = await api("/api/suggest-levels", {
          method: "POST",
          body: JSON.stringify({ signal_id: signal.id })
        });
        if (s.stop)   el("stopPrice").value   = s.stop;
        if (s.target) el("targetPrice").value = s.target;
        const hasLevels = s.stop && s.target;
        el("decisionBox").innerHTML = hasLevels
          ? `<div class="muted">Stop &amp; target auto-filled from signal levels. Adjust if needed, then run Coach Check.</div>`
          : `<div class="muted">No level data on this signal — enter stop and target manually, then run Coach Check.</div>`;
        updateRewardRisk();
      } catch (_) {
        el("decisionBox").innerHTML = `<div class="muted">Enter stop and target, then run Coach Check.</div>`;
      }
    }

    function planPayload() {
      if (!state.selected) throw new Error("Select a signal first.");
      return {
        signal_id: state.selected.id,
        entry_price: el("entryPrice").value,
        stop_price: el("stopPrice").value,
        target_price: el("targetPrice").value,
        setup_grade: el("setupGrade").value,
        account_equity: el("accountEquity").value,
        notes: el("notes").value
      };
    }

    async function coachCheck() {
      try {
        const data = await api("/api/coach-check", {
          method: "POST",
          body: JSON.stringify(planPayload())
        });
        state.lastDecision = data;
        renderDecision(data);
      } catch (error) {
        renderError(error.message);
      }
    }

    async function savePlan() {
      try {
        const data = await api("/api/save-plan", {
          method: "POST",
          body: JSON.stringify(planPayload())
        });
        state.lastDecision = data;
        renderDecision(data);
        await loadState();
      } catch (error) {
        renderError(error.message);
      }
    }

    async function expireSignal() {
      if (!state.selected) {
        renderError("Select a signal first.");
        return;
      }
      try {
        await api("/api/signal-status", {
          method: "POST",
          body: JSON.stringify({ signal_id: state.selected.id, status: "expired" })
        });
        await loadState();
      } catch (error) {
        renderError(error.message);
      }
    }

    function renderDecision(data) {
      const decision = data.decision || {};
      const plan = data.plan || {};
      const approved = decision.approved === true;
      el("decisionBadge").textContent = approved ? "Approved" : "Rejected";
      el("decisionBadge").className = "badge " + (approved ? "good" : "bad");
      el("submitBtn").disabled = !approved;
      const reasons = (decision.reasons || []).map(r => `<li>${escapeHtml(r)}</li>`).join("");
      const warnings = (decision.warnings || []).map(w => `<li>${escapeHtml(w)}</li>`).join("");
      el("decisionBox").innerHTML = `
        <div class="decision-title ${approved ? "approved" : "rejected"}">${approved ? "Approved" : "Rejected"} | Grade ${escapeHtml(decision.grade || "")}</div>
        <div>Reward/Risk: <strong>${escapeHtml(String(plan.reward_to_risk ?? ""))}R</strong></div>
        <div>Risk budget: <strong>$${decision.sized_risk_dollars ?? "n/a"}</strong></div>
        ${reasons ? `<div class="small"><strong>Reasons</strong><ul>${reasons}</ul></div>` : ""}
        ${warnings ? `<div class="small"><strong>Warnings</strong><ul>${warnings}</ul></div>` : ""}
      `;
    }

    async function submitOrder() {
      try {
        el("submitBtn").disabled = true;
        el("submitBtn").textContent = "Submitting...";
        const data = await api("/api/submit-order", {
          method: "POST",
          body: JSON.stringify(planPayload())
        });
        const order = data.order || {};
        el("decisionBadge").textContent = "Order Sent";
        el("decisionBadge").className = "badge good";
        el("decisionBox").innerHTML = `
          <div class="decision-title approved">Order submitted to Alpaca</div>
          <div>Symbol: <strong>${escapeHtml(order.symbol || "")}</strong> | Side: ${escapeHtml(order.side || "")}</div>
          <div>Shares: <strong>${order.shares ?? "n/a"}</strong> | Entry: $${order.entry ?? ""} | Stop: $${order.stop ?? ""} | Target: $${order.target ?? ""}</div>
          <div>Risk: <strong>$${order.risk_dollars ?? "n/a"}</strong></div>
          <div class="small muted">Order ID: ${escapeHtml(order.order_id || "")} | Status: ${escapeHtml(order.order_status || "")}</div>
        `;
        await loadState();
      } catch (error) {
        el("submitBtn").disabled = false;
        el("submitBtn").textContent = "Submit Order";
        renderError(error.message);
      }
    }

    function renderError(message) {
      el("decisionBadge").textContent = "Error";
      el("decisionBadge").className = "badge bad";
      el("decisionBox").innerHTML = `<div class="decision-title rejected">Action failed</div><div>${escapeHtml(message)}</div>`;
    }

    function renderRules(rules, policy) {
      const lines = [
        `Mode: ${policy.mode || "paper"}`,
        `Risk/trade: ${percent(rules.risk_per_trade_pct)}`,
        `Max daily loss: ${percent(rules.max_daily_loss_pct)}`,
        `Max trades/day: ${rules.max_trades_per_day ?? "n/a"}`,
        `Minimum R/R: ${rules.minimum_reward_to_risk ?? "n/a"}`
      ];
      el("rulesBox").innerHTML = lines.map(escapeHtml).join("<br>");
    }

    function renderJournal() {
      el("journalCount").textContent = `${state.trades.length} trades`;
      if (!state.trades.length) {
        el("journalTable").innerHTML = `<div class="empty">No journal entries yet.</div>`;
        return;
      }
      const rows = state.trades.slice(0, 12).map(t => {
        const plan = t.plan || {};
        const decision = t.coach_decision || {};
        return `
          <tr>
            <td>${escapeHtml(t.date || "")}</td>
            <td><strong>${escapeHtml(plan.symbol || "")}</strong><br><span class="small muted">${escapeHtml(plan.setup || "")}</span></td>
            <td>${escapeHtml(t.status || "")}<br><span class="small muted">${escapeHtml(t.outcome || "")}</span></td>
            <td>${escapeHtml(String(plan.reward_to_risk ?? ""))}R</td>
            <td>${decision.approved ? "Yes" : "No"}<br><span class="small muted">${escapeHtml(decision.grade || "")}</span></td>
          </tr>
        `;
      }).join("");
      el("journalTable").innerHTML = `
        <table>
          <thead><tr><th>Date</th><th>Signal</th><th>Status</th><th>R/R</th><th>Coach</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    function updateRewardRisk() {
      const entry = Number(el("entryPrice").value);
      const stop = Number(el("stopPrice").value);
      const target = Number(el("targetPrice").value);
      const side = el("side").value;
      let risk = 0;
      let reward = 0;
      if (side === "short") {
        risk = stop - entry;
        reward = entry - target;
      } else {
        risk = entry - stop;
        reward = target - entry;
      }
      el("rewardRisk").value = risk > 0 ? `${(reward / risk).toFixed(2)}R` : "";
    }

    function percent(value) {
      if (value === undefined || value === null || value === "") return "n/a";
      return `${(Number(value) * 100).toFixed(2)}%`;
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    el("refreshBtn").addEventListener("click", loadState);
    el("signalFilter").addEventListener("change", renderSignals);
    el("coachBtn").addEventListener("click", coachCheck);
    el("submitBtn").addEventListener("click", submitOrder);
    el("saveBtn").addEventListener("click", savePlan);
    el("expireBtn").addEventListener("click", expireSignal);

    el("reviewBtn").addEventListener("click", async () => {
      el("reviewBtn").textContent = "Building…";
      el("reviewBtn").disabled = true;
      try {
        const data = await api("/api/daily-review", { method: "POST", body: "{}" });
        renderReview(data.review || {});
      } catch (err) {
        el("reviewBox").innerHTML = `<div class="muted">Error: ${escapeHtml(err.message)}</div>`;
      } finally {
        el("reviewBtn").textContent = "Build Review";
        el("reviewBtn").disabled = false;
      }
    });

    function renderReview(r) {
      const na = v => (v === null || v === undefined) ? "n/a" : v;
      const pct = v => (v === null || v === undefined) ? "n/a" : `${(v * 100).toFixed(0)}%`;
      const mistakes = Object.entries(r.mistake_counts || {})
        .sort((a, b) => b[1] - a[1])
        .map(([tag, n]) => `<li>${escapeHtml(tag)} ×${n}</li>`).join("") || "<li>None</li>";
      el("reviewBox").innerHTML = `
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px;">
          <div class="decision-box" style="min-height:auto;text-align:center">
            <div class="small muted">Trades</div><div style="font-size:20px;font-weight:700">${na(r.trades)}</div>
          </div>
          <div class="decision-box" style="min-height:auto;text-align:center">
            <div class="small muted">Win Rate</div><div style="font-size:20px;font-weight:700">${pct(r.win_rate)}</div>
          </div>
          <div class="decision-box" style="min-height:auto;text-align:center">
            <div class="small muted">Total R</div><div style="font-size:20px;font-weight:700;color:${(r.total_r||0)>=0?'var(--good)':'var(--danger)'}">${na(r.total_r)}R</div>
          </div>
          <div class="decision-box" style="min-height:auto;text-align:center">
            <div class="small muted">Avg R</div><div style="font-size:20px;font-weight:700">${na(r.average_r)}R</div>
          </div>
        </div>
        <div class="decision-box" style="margin-bottom:8px">
          <div class="decision-title" style="color:var(--accent)">Coach Focus</div>
          <div>${escapeHtml(r.coach_focus || "")}</div>
        </div>
        ${Object.keys(r.mistake_counts || {}).length ? `
        <div class="decision-box">
          <div class="decision-title">Mistake Tags</div>
          <ul class="small" style="margin:4px 0 0 16px;padding:0">${mistakes}</ul>
        </div>` : ""}
        <div class="small muted" style="margin-top:8px">Date: ${escapeHtml(r.date || "")} | ${r.wins ?? 0}W / ${r.losses ?? 0}L</div>
      `;
    }

    el("addSignalToggle").addEventListener("click", () => {
      const form = el("addSignalForm");
      const visible = form.style.display !== "none";
      form.style.display = visible ? "none" : "block";
      if (!visible) el("manualSymbol").focus();
    });
    el("addSignalCancel").addEventListener("click", () => {
      el("addSignalForm").style.display = "none";
    });
    el("addSignalSubmit").addEventListener("click", async () => {
      const btn = el("addSignalSubmit");
      btn.disabled = true;
      btn.textContent = "Adding…";
      try {
        await api("/api/add-signal", {
          method: "POST",
          body: JSON.stringify({
            symbol:    el("manualSymbol").value,
            price:     el("manualPrice").value,
            setup:     el("manualSetup").value,
            timeframe: el("manualTimeframe").value,
            notes:     el("manualNotes").value,
          })
        });
        el("manualSymbol").value = "";
        el("manualPrice").value = "";
        el("manualNotes").value = "";
        el("addSignalForm").style.display = "none";
        await loadState();
      } catch (err) {
        alert("Could not add signal: " + err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "Add to Inbox";
      }
    });
    ["entryPrice", "stopPrice", "targetPrice"].forEach(id => el(id).addEventListener("input", updateRewardRisk));
    loadState().catch(error => {
      el("statusLine").textContent = error.message;
    });
  </script>
</body>
</html>
"""


_DEFAULT_CERT = os.path.join(_DIR, "certs", "trade_manager_ui.crt")
_DEFAULT_KEY = os.path.join(_DIR, "certs", "trade_manager_ui.key")


def run_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    certfile: str | None = None,
    keyfile: str | None = None,
) -> None:
    import ssl

    server = ThreadingHTTPServer((host, port), TradeManagerUiHandler)

    if certfile and keyfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    else:
        scheme = "http"

    _log(f"[TradeManagerUI] Listening on {scheme}://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local trade manager coach UI")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cert", default=_DEFAULT_CERT, help="TLS certificate file")
    parser.add_argument("--key", default=_DEFAULT_KEY, help="TLS private key file")
    parser.add_argument("--no-tls", action="store_true", help="Disable TLS even if cert exists")
    args = parser.parse_args()

    certfile = None
    keyfile = None
    if not args.no_tls and os.path.exists(args.cert) and os.path.exists(args.key):
        certfile = args.cert
        keyfile = args.key
    elif not args.no_tls and (args.cert != _DEFAULT_CERT or args.key != _DEFAULT_KEY):
        raise SystemExit(f"Cert or key not found: {args.cert}, {args.key}")

    run_server(args.host, args.port, certfile, keyfile)


if __name__ == "__main__":
    main()
