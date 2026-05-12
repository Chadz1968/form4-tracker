"""
start_servers.py

Start all day trading manager servers with a single command.

  python start_servers.py

Starts:
  Trade Manager UI      https://127.0.0.1:8787  (http if no cert)
  TradingView Webhook   http://127.0.0.1:8765
  Market Scanner        (scans at market open, idles outside hours)
  ngrok tunnel          exposes webhook port to TradingView

Press Ctrl+C to stop everything cleanly.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

_DIR = Path(__file__).parent
PYTHON = sys.executable

_CERT = _DIR / "certs" / "trade_manager_ui.crt"
_KEY  = _DIR / "certs" / "trade_manager_ui.key"
_TLS  = _CERT.exists() and _KEY.exists()
_UI_SCHEME = "https" if _TLS else "http"
_UI_URL = f"{_UI_SCHEME}://127.0.0.1:8787"

_NGROK_SEARCH = [
    shutil.which("ngrok"),
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
        / "Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe/ngrok.exe"),
    r"C:\Program Files\ngrok\ngrok.exe",
    str(Path.home() / "ngrok" / "ngrok.exe"),
]


def _find_ngrok() -> str | None:
    for path in _ngrok_search():
        if path and Path(path).exists():
            return path
    return None


def _ngrok_search() -> list[str | None]:
    return _NGROK_SEARCH


def _health(url: str, timeout: int = 3) -> bool:
    try:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_healthy(name: str, url: str, retries: int = 10, delay: float = 0.5) -> bool:
    for _ in range(retries):
        if _health(url):
            return True
        time.sleep(delay)
    print(f"  [WARN] {name} did not become healthy at {url}")
    return False


def _ngrok_url() -> str | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3) as r:
            data = json.loads(r.read())
        https = [t["public_url"] for t in data.get("tunnels", []) if t.get("proto") == "https"]
        return https[0] if https else None
    except Exception:
        return None


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    processes: list[subprocess.Popen] = []

    def _shutdown(sig=None, frame=None):
        print("\n\nShutting down all servers...")
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass
        for p in processes:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        print("All stopped. Goodbye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    os.chdir(_DIR)
    print("=" * 56)
    print("  Trade Manager Coach — starting servers")
    print("=" * 56)

    # --- Trade Manager UI ---
    if _port_in_use(8787):
        print(f"  UI server      already running on :8787")
    else:
        scheme_label = "https" if _TLS else "http (no cert found)"
        print(f"  Starting UI server on :8787 [{scheme_label}] ...", end=" ", flush=True)
        p = subprocess.Popen(
            [PYTHON, "-u", "trade_manager_ui.py", "--host", "127.0.0.1", "--port", "8787"],
            cwd=_DIR,
        )
        processes.append(p)
        ok = _wait_healthy("UI", f"{_UI_URL}/health", retries=12)
        print("OK" if ok else "WARN — check logs")

    # --- TradingView Webhook ---
    if _port_in_use(8765):
        print("  Webhook server already running on :8765")
    else:
        print("  Starting webhook server on :8765 ...", end=" ", flush=True)
        p = subprocess.Popen(
            [PYTHON, "-u", "tradingview_webhook.py", "--host", "127.0.0.1", "--port", "8765"],
            cwd=_DIR,
        )
        processes.append(p)
        ok = _wait_healthy("Webhook", "http://127.0.0.1:8765/health")
        print("OK" if ok else "WARN — check logs")

    # --- Market Scanner ---
    print("  Starting market scanner ...", end=" ", flush=True)
    p = subprocess.Popen([PYTHON, "-u", "market_scanner.py"], cwd=_DIR)
    processes.append(p)
    time.sleep(1.5)
    if p.poll() is None:
        print("OK (idle outside market hours)")
    else:
        print(f"WARN — exited with code {p.returncode}")

    # --- ngrok ---
    ngrok = _find_ngrok()
    if not ngrok:
        print("  ngrok          not found — webhook tunnel inactive")
        print("                 Run: ngrok http 8765  in a separate terminal")
        webhook_url = None
    elif _port_in_use(4040):
        print("  ngrok          already running", end=" ", flush=True)
        webhook_url = _ngrok_url()
        print(f"→ {webhook_url}" if webhook_url else "(URL unknown)")
    else:
        print("  Starting ngrok tunnel on :8765 ...", end=" ", flush=True)
        p = subprocess.Popen([ngrok, "http", "8765"], cwd=_DIR)
        processes.append(p)
        time.sleep(4)
        webhook_url = _ngrok_url()
        if webhook_url:
            print(f"OK → {webhook_url}")
        else:
            print("WARN — could not read tunnel URL from ngrok API")

    # --- Summary ---
    print()
    print("=" * 56)
    print("  READY")
    print("=" * 56)
    print(f"  Coach UI      {_UI_URL}")
    if webhook_url:
        print(f"  Webhook URL   {webhook_url}/webhook/tradingview")
    print()
    print("  Ctrl+C to stop all servers")
    print("=" * 56)

    webbrowser.open(_UI_URL)

    # Keep alive — child processes run independently
    try:
        while True:
            time.sleep(30)
            # Warn if any managed process has died unexpectedly
            for p in list(processes):
                if p.poll() is not None:
                    print(f"  [WARN] A server process (PID {p.pid}) exited unexpectedly "
                          f"with code {p.returncode}. Restart with: python start_servers.py")
                    processes.remove(p)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
