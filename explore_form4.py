"""
explore_form4.py  —  entry point (delegates to finder_agent + filter_agent)

Usage:
    python explore_form4.py --date 2025-04-28
"""

import argparse
import datetime

import config
from filter_agent import run
from keep_awake import keep_system_awake

if __name__ == "__main__":
    config.validate()

    parser = argparse.ArgumentParser(
        description="Scan EDGAR Form 4 filings for insider purchases"
    )
    parser.add_argument(
        "--date",
        default=datetime.date.today().isoformat(),
        help="Scan date in YYYY-MM-DD format",
    )
    args = parser.parse_args()

    with keep_system_awake("Form 4 scan"):
        run(args.date)
