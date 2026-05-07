"""
Configuration and environment management.
All secrets loaded from .env; never hardcoded.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Credentials ---
OPENAI_KEY        = os.getenv("OPENAI_API_KEY")
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
EDGAR_USER_AGENT  = os.getenv("EDGAR_USER_AGENT")

# --- Signal Parameters ---
LOOKBACK_DAYS        = 30        # cluster-buy detection window
MIN_BUY_DOLLARS      = 50_000    # ignore token purchases below this
CLUSTER_MIN_INSIDERS = 2         # insiders within lookback to qualify as cluster
MIN_LLM_SCORE        = 4.0       # minimum composite score (1-10) to pass to risk agent

# --- Risk Parameters ---
RISK_PER_TRADE = 0.01    # fraction of equity risked per trade (1%)
MAX_DRAWDOWN   = 0.10    # cumulative drawdown limit before trading halts (10%)
STOP_PCT       = 0.12    # stop distance from entry (12% for multi-week holds)


def get_trading_client():
    """Return an Alpaca TradingClient pointed at the paper trading endpoint."""
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=ALPACA_API_KEY,
        secret_key=ALPACA_SECRET_KEY,
        paper=True,
    )


def validate():
    """Raise clearly if required config is missing. Call at script startup."""
    missing = []
    if not EDGAR_USER_AGENT:
        missing.append("EDGAR_USER_AGENT")
    if not OPENAI_KEY:
        missing.append("OPENAI_API_KEY")
    if not ALPACA_API_KEY:
        missing.append("ALPACA_API_KEY")
    if not ALPACA_SECRET_KEY:
        missing.append("ALPACA_SECRET_KEY")
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Check your .env file."
        )