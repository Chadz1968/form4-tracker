"""
Configuration and environment management.
All secrets loaded from .env; never hardcoded.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Credentials ---
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# Alpaca credentials — kept for future paper-trading integration.
# Not used yet; research phase only.
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# --- EDGAR Configuration ---
# EDGAR requires a User-Agent identifying the requester.
# See https://www.sec.gov/os/accessing-edgar-data
EDGAR_USER_AGENT = os.getenv("EDGAR_USER_AGENT")

# --- Signal Parameters ---
# These are starting points to test, not proven values.
# Every number here is a hypothesis to validate.

LOOKBACK_DAYS = 30            # cluster-buy detection window
MIN_BUY_DOLLARS = 50_000      # ignore token purchases below this
CLUSTER_MIN_INSIDERS = 2      # number of insiders within LOOKBACK_DAYS to qualify as cluster

# --- Risk Parameters (unused in research phase, kept for later) ---
RISK_PER_TRADE = 0.01         # 1% of account per position
MAX_DRAWDOWN = 0.10           # 10% — wider than gap-fade given longer holds
STOP_PCT = 0.12               # 12% stop for multi-week holds (volatility-dependent)

# --- Validation ---
def validate():
    """Raise clearly if required config is missing. Call at script startup."""
    missing = []
    if not EDGAR_USER_AGENT:
        missing.append("EDGAR_USER_AGENT")
    if not OPENAI_KEY:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Check your .env file."
        )