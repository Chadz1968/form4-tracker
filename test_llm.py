import argparse
from filter_agent import get_candidates
from llm_filter_agent import score_candidates, print_scored

parser = argparse.ArgumentParser()
parser.add_argument("--date", default="2025-04-28", help="Scan date YYYY-MM-DD")
args = parser.parse_args()

print(f"\n=== STEP 1: Finder + Filter ===")
candidates = get_candidates(args.date)
total = sum(len(v) for v in candidates.values())
print(f"[Filter] {len(candidates)} tickers, {total} qualifying purchases\n")

if not candidates:
    print("No candidates found — try a different date (market days only).")
else:
    print(f"=== STEP 2: LLM Scoring ({total} candidates) ===\n")
    results = score_candidates(candidates)
    print_scored(results)
