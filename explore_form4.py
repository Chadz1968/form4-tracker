"""
explore_form4.py  —  entry point (delegates to finder_agent + filter_agent)

Usage:
    python explore_form4.py --date 2025-04-28
"""

import argparse
from filter_agent import run

parser = argparse.ArgumentParser(
    description="Scan EDGAR Form 4 filings for insider purchases"
)
parser.add_argument(
    "--date",
    default="2025-04-28",
    help="Scan date in YYYY-MM-DD format",
)
args = parser.parse_args()

run(args.date)