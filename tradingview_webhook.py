"""
tradingview_webhook.py

Small standard-library webhook server for TradingView alerts.

It receives JSON alerts, validates the fields the manager needs, optionally
checks a shared secret, and stores valid alerts in signal_inbox.json through
trade_manager_journal.ingest_signal().

Run:
  python tradingview_webhook.py --host 127.0.0.1 --port 8765

Endpoint:
  POST /webhook/tradingview
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import trade_manager_journal as journal


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64_000
ALLOWED_SIDES = {"long", "short"}


def _load_secret() -> str | None:
    """Read optional webhook secret from env or .env without requiring dotenv."""
    secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET")
    if secret:
        return secret

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return None
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == "TRADINGVIEW_WEBHOOK_SECRET":
                return value.strip().strip('"').strip("'") or None
    return None


def normalize_tradingview_alert(payload: dict[str, Any]) -> dict:
    """
    Convert TradingView JSON into the manager signal shape.

    Required fields: symbol, setup, side, price, timeframe, trigger.
    source defaults to "tradingview".
    """
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    required = ["symbol", "setup", "side", "price", "timeframe", "trigger"]
    missing = [field for field in required if payload.get(field) in (None, "")]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    side = str(payload["side"]).lower()
    if side not in ALLOWED_SIDES:
        raise ValueError(f"Unsupported side: {side}")

    try:
        price = float(payload["price"])
    except (TypeError, ValueError):
        raise ValueError("Price must be numeric.")
    if price <= 0:
        raise ValueError("Price must be positive.")

    normalized = {
        "source": str(payload.get("source") or "tradingview").lower(),
        "symbol": str(payload["symbol"]).upper().strip(),
        "timeframe": str(payload["timeframe"]).strip(),
        "setup": str(payload["setup"]).strip(),
        "side": side,
        "price": price,
        "trigger": str(payload["trigger"]).strip(),
        "notes": str(payload.get("notes") or ""),
        "alert_id": payload.get("alert_id"),
        "bar_time": payload.get("bar_time"),
        "received_secret": payload.get("secret"),
    }

    if not normalized["symbol"]:
        raise ValueError("Symbol is blank after normalization.")
    if normalized["source"] != "tradingview":
        raise ValueError("Only TradingView alerts are accepted by this endpoint.")
    return normalized


def is_duplicate_alert(normalized: dict, inbox: list[dict] | None = None) -> bool:
    """Reject repeats by alert_id when provided, otherwise by stable alert fingerprint."""
    inbox = inbox if inbox is not None else journal._load_json(journal.SIGNAL_INBOX_FILE, [])
    alert_id = normalized.get("alert_id")
    if alert_id:
        return any((s.get("raw_payload") or {}).get("alert_id") == alert_id for s in inbox)

    fingerprint = _fingerprint(normalized)
    for signal in inbox:
        raw = signal.get("raw_payload") or {}
        candidate = {
            "symbol": signal.get("symbol"),
            "setup": signal.get("setup"),
            "side": signal.get("side"),
            "trigger": signal.get("trigger"),
            "timeframe": signal.get("timeframe"),
            "bar_time": raw.get("bar_time"),
        }
        if _fingerprint(candidate) == fingerprint:
            return True
    return False


def _fingerprint(payload: dict) -> tuple:
    return (
        str(payload.get("symbol") or "").upper(),
        str(payload.get("setup") or ""),
        str(payload.get("side") or "").lower(),
        str(payload.get("trigger") or ""),
        str(payload.get("timeframe") or ""),
        str(payload.get("bar_time") or ""),
    )


def handle_tradingview_alert(
    payload: dict[str, Any],
    header_secret: str | None = None,
    expected_secret: str | None = None,
) -> tuple[int, dict]:
    """
    Validate and ingest one alert.

    Returns (http_status, response_payload).
    """
    expected_secret = expected_secret if expected_secret is not None else _load_secret()
    if expected_secret:
        supplied = header_secret or payload.get("secret")
        if supplied != expected_secret:
            return 401, {"ok": False, "error": "Invalid webhook secret."}

    try:
        normalized = normalize_tradingview_alert(payload)
        if is_duplicate_alert(normalized):
            return 200, {"ok": True, "status": "duplicate", "message": "Alert already stored."}

        signal = journal.ingest_signal({
            "source": normalized["source"],
            "symbol": normalized["symbol"],
            "timeframe": normalized["timeframe"],
            "setup": normalized["setup"],
            "side": normalized["side"],
            "price": normalized["price"],
            "trigger": normalized["trigger"],
            "notes": normalized["notes"],
            "alert_id": normalized.get("alert_id"),
            "bar_time": normalized.get("bar_time"),
        })
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

    return 201, {
        "ok": True,
        "status": "stored",
        "signal_id": signal["id"],
        "symbol": signal["symbol"],
        "setup": signal["setup"],
    }


class TradingViewWebhookHandler(BaseHTTPRequestHandler):
    server_version = "TradingViewWebhook/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"ok": True, "service": "tradingview_webhook"})
            return
        self._send_json(404, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/webhook/tradingview":
            self._send_json(404, {"ok": False, "error": "Not found."})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "Empty request body."})
            return
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "Request body too large."})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Body must be valid JSON."})
            return

        status, response = handle_tradingview_alert(
            payload,
            header_secret=self.headers.get("X-Webhook-Secret"),
        )
        self._send_json(status, response)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[TradingViewWebhook] {self.address_string()} - {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), TradingViewWebhookHandler)
    print(f"[TradingViewWebhook] Listening on http://{host}:{port}")
    print("[TradingViewWebhook] POST alerts to /webhook/tradingview")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView webhook intake server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
