"""
llm_filter_agent.py

Scores qualifying insider-buy signals using a five-dimension framework
grounded in academic insider-trading research and systematic hedge fund practice.

Scoring dimensions and weights:
  conviction (25%) — purchase size vs. estimated compensation (Seyhun 1986)
  timing     (25%) — buying into price weakness; contrarian signal
  role       (20%) — CEO/CFO carry more material non-public information
  cluster    (15%) — multiple insiders amplifies conviction (Cohen et al. 2012)
  thesis     (15%) — does available context support a coherent bullish thesis?

Role and cluster are scored deterministically in Python.
Conviction, timing, and thesis are scored by GPT-4o with structured JSON output.

Usage:
    from llm_filter_agent import score_candidates, print_scored
    results = score_candidates(ticker_purchases)   # dict from filter_agent
    print_scored(results)
"""

import json
from openai import OpenAI
import yfinance as yf

from config import OPENAI_KEY

# ── Dimension weights (must sum to 1.0) ──────────────────────
WEIGHTS = {
    "conviction": 0.25,
    "timing":     0.25,
    "role":       0.20,
    "cluster":    0.15,
    "thesis":     0.15,
}

# ── Role tiers (deterministic, no LLM tokens wasted) ─────────
_ROLE_TIER_3 = ["ceo", "cfo", "chairman", "president"]
_ROLE_TIER_2 = ["evp", "svp", "chief", "vice president", "vp"]

_client = OpenAI(api_key=OPENAI_KEY)


def _role_score(position: str) -> int:
    p = position.lower()
    if any(kw in p for kw in _ROLE_TIER_3):
        return 3
    if any(kw in p for kw in _ROLE_TIER_2):
        return 2
    return 1


def _get_price_context(ticker: str) -> dict:
    try:
        stock = yf.Ticker(ticker)
        info  = stock.info
        hist  = stock.history(period="3mo")

        current = info.get("currentPrice") or info.get("regularMarketPrice")
        high52  = info.get("fiftyTwoWeekHigh")
        low52   = info.get("fiftyTwoWeekLow")

        pct_from_high = None
        if current and high52:
            pct_from_high = round((current - high52) / high52 * 100, 1)

        three_mo_chg = None
        if not hist.empty and len(hist) >= 2:
            start = float(hist["Close"].iloc[0])
            end   = float(hist["Close"].iloc[-1])
            if start:
                three_mo_chg = round((end - start) / start * 100, 1)

        return {
            "current_price":      current,
            "52w_high":           high52,
            "52w_low":            low52,
            "pct_from_52w_high":  pct_from_high,
            "3mo_change_pct":     three_mo_chg,
            "market_cap":         info.get("marketCap"),
            "sector":             info.get("sector", "Unknown"),
            "industry":           info.get("industry", "Unknown"),
            "pe_ratio":           info.get("trailingPE"),
            "forward_pe":         info.get("forwardPE"),
            "pb_ratio":           info.get("priceToBook"),
        }
    except Exception:
        return {}


_SCORE_PROMPT = """\
You are a systematic insider-signal analyst at a long-short equity hedge fund.
Evaluate this insider purchase across three dimensions and respond with valid JSON only.

COMPANY: {company} ({ticker})
SECTOR / INDUSTRY: {sector} / {industry}
MARKET CAP: {market_cap}

INSIDER: {insider} — {position}
PURCHASE VALUE: ${value:,.0f}
AVG PRICE PAID: ${avg_price:.2f}
INSIDERS BUYING SAME DAY: {cluster_size}

PRICE CONTEXT:
  Current price:       {current_price}
  52-week high:        {high52}
  52-week low:         {low52}
  % from 52w high:     {pct_from_high}%
  3-month price move:  {three_mo}%
  Trailing P/E:        {pe}
  Price/Book:          {pb}

SCORING GUIDE — each dimension scored 1, 2, or 3:

conviction_score — how meaningful is the purchase size vs. expected compensation?
  Use the insider's ROLE to estimate comp, not just market cap.

  C-suite (CEO, CFO, President, Chairman) — estimate from market cap:
    < $300M small-cap:  ~$800K–$1.5M/yr
    $300M–$2B mid-cap:  ~$2M–$5M/yr
    $2B–$10B large-cap: ~$5M–$15M/yr
    > $10B mega-cap:    ~$15M+/yr

  Directors (board members, non-executive) — regardless of company size,
  board retainers are typically $150K–$350K/yr in cash + equity. A director
  buying $200K+ in open-market stock is already 50–100% of their annual cash
  comp and should be treated as high conviction.

  VP / SVP / EVP — intermediate; use 40–60% of C-suite estimate for same cap tier.

  3 = Purchase > 50% of estimated annual cash comp — very high conviction
  2 = Purchase 15–50% of estimated cash comp — meaningful signal
  1 = Purchase < 15% of estimated cash comp — token or routine, low signal

timing_score — is the insider buying into price weakness (contrarian)?
  3 = Stock > 20% below 52-week high — clear contrarian buy into weakness
  2 = Stock 10–20% below 52-week high — moderate weakness
  1 = Stock within 10% of 52-week high — buying near strength, less predictive

thesis_score — does the available context support a coherent bullish thesis?
  IMPORTANT — sector-specific valuation rules:
  - REITs (Real Estate sector): P/E is meaningless due to depreciation accounting.
    Use P/B ratio instead. P/B < 1.5 is cheap for a REIT; P/B > 2.5 is expensive.
    Do NOT flag high P/E as a red flag for REITs.
  - Banks / Regional Financials: P/E < 12 is cheap; P/B < 1.2 is cheap.
    These are value sectors — low multiples support a stronger thesis.
  - Energy: cyclical; focus on whether the sector is in a down cycle (supports thesis)
    rather than absolute P/E.
  - All other sectors: use trailing P/E and P/B normally.

  3 = Strong: cheap valuation for the sector, buying into sector-wide selloff,
      or clear value-oriented sector at lows
  2 = Plausible: no obvious red flags, numbers look reasonable for the sector
  1 = Weak or flag present: expensive valuation with no growth story, stock near
      highs, company in distressed sector, purchase looks like propping

Also provide:
  reasoning — one focused paragraph referencing specific numbers. If there are red
    flags, name them explicitly. Be direct; this goes to a portfolio manager.
  red_flags — array of short strings (empty if none)

Return this exact JSON structure (no other text):
{{
  "conviction_score": <1|2|3>,
  "timing_score": <1|2|3>,
  "thesis_score": <1|2|3>,
  "reasoning": "<paragraph>",
  "red_flags": []
}}"""


def _llm_scores(purchase: dict, price_ctx: dict, cluster_size: int) -> dict:
    market_cap = price_ctx.get("market_cap")
    cap_str = f"${market_cap:,.0f}" if market_cap else "unknown"

    prompt = _SCORE_PROMPT.format(
        company      = purchase["company"],
        ticker       = purchase["ticker"],
        sector       = price_ctx.get("sector", "unknown"),
        industry     = price_ctx.get("industry", "unknown"),
        market_cap   = cap_str,
        insider      = purchase["insider"],
        position     = purchase["position"],
        value        = purchase["value"],
        avg_price    = purchase["avg_price"],
        cluster_size = cluster_size,
        current_price= price_ctx.get("current_price", "N/A"),
        high52       = price_ctx.get("52w_high", "N/A"),
        low52        = price_ctx.get("52w_low", "N/A"),
        pct_from_high= price_ctx.get("pct_from_52w_high", "N/A"),
        three_mo     = price_ctx.get("3mo_change_pct", "N/A"),
        pe           = price_ctx.get("pe_ratio", "N/A"),
        pb           = price_ctx.get("pb_ratio", "N/A"),
    )

    response = _client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        max_tokens=450,
        temperature=0.2,
    )
    return json.loads(response.choices[0].message.content)


def _composite(llm: dict, role: int, cluster: int) -> float:
    """Weighted average of five 1-3 scores, normalised to 1–10."""
    raw = (
        llm["conviction_score"] * WEIGHTS["conviction"] +
        llm["timing_score"]     * WEIGHTS["timing"] +
        role                    * WEIGHTS["role"] +
        cluster                 * WEIGHTS["cluster"] +
        llm["thesis_score"]     * WEIGHTS["thesis"]
    )
    return round((raw - 1) / 2 * 9 + 1, 1)


def score_candidates(ticker_purchases: dict) -> list[dict]:
    """
    Score all qualifying purchases and collapse clusters to one row per ticker.

    Args:
        ticker_purchases: {ticker: [purchase_dict, ...]} — output of
                          filter_agent.get_candidates()

    Returns:
        One dict per ticker, sorted by llm_score descending. Cluster rows use
        the lead insider's dimension scores (highest individual llm_score) and
        aggregate the total value across all insiders. Each dict gains:
        llm_score, role_score, cluster_score, conviction_score, timing_score,
        thesis_score, reasoning, red_flags, price_context, cluster_size,
        cluster_members (None for single buys, list of dicts for clusters).
    """
    scored_by_ticker: dict[str, list[dict]] = {}

    for ticker, purchases in ticker_purchases.items():
        cluster_size  = len(purchases)
        cluster_score = 3 if cluster_size >= 2 else 1

        print(f"[LLM] {ticker} — fetching price context...")
        price_ctx = _get_price_context(ticker)

        scored_by_ticker[ticker] = []
        for purchase in purchases:
            role = _role_score(purchase["position"])

            print(f"[LLM] {ticker} — scoring {purchase['insider']}...")
            try:
                llm = _llm_scores(purchase, price_ctx, cluster_size)
            except Exception as e:
                print(f"[LLM] Scoring failed for {ticker}: {e}")
                continue

            scored_by_ticker[ticker].append({
                **purchase,
                "cluster_size":      cluster_size,
                "role_score":        role,
                "cluster_score":     cluster_score,
                "conviction_score":  llm.get("conviction_score"),
                "timing_score":      llm.get("timing_score"),
                "thesis_score":      llm.get("thesis_score"),
                "llm_score":         _composite(llm, role, cluster_score),
                "reasoning":         llm.get("reasoning", ""),
                "red_flags":         llm.get("red_flags", []),
                "price_context":     price_ctx,
            })

    # Collapse: one row per ticker
    collapsed = []
    for ticker, entries in scored_by_ticker.items():
        if not entries:
            continue

        if len(entries) == 1:
            entries[0]["cluster_members"] = None
            collapsed.append(entries[0])
        else:
            # Lead = highest individual llm_score (best role + conviction)
            lead = max(entries, key=lambda x: (x["role_score"], x["conviction_score"]))
            collapsed.append({
                **lead,
                "value":           sum(e["value"] for e in entries),
                "cluster_members": sorted(
                    [{"insider": e["insider"], "position": e["position"],
                      "value": e["value"], "conviction_score": e["conviction_score"]}
                     for e in entries],
                    key=lambda x: x["value"], reverse=True,
                ),
            })

    collapsed.sort(key=lambda x: x["llm_score"], reverse=True)
    return collapsed


def print_scored(results: list[dict]) -> None:
    n_signals  = len(results)
    n_clusters = sum(1 for r in results if r.get("cluster_members"))
    print(f"\n{'='*60}")
    print(f"LLM SCORED SIGNALS — {n_signals} signals ({n_clusters} clusters)")
    print(f"{'='*60}\n")

    for r in results:
        members    = r.get("cluster_members")
        is_cluster = bool(members)
        flags_str  = f"  FLAGS: {' | '.join(r['red_flags'])}" if r["red_flags"] else ""

        if is_cluster:
            print(f"  [{r['llm_score']:.1f}/10] {r['company']} ({r['ticker']})  "
                  f"[CLUSTER x{r['cluster_size']}]")
            print(f"  Total cluster value: ${r['value']:,.0f}")
            print(f"  Lead: {r['insider']} — {r['position']}")
            for m in members:
                print(f"    -> {m['insider']} ({m['position']}): "
                      f"${m['value']:,.0f}  conviction={m['conviction_score']}")
        else:
            print(f"  [{r['llm_score']:.1f}/10] {r['company']} ({r['ticker']})")
            print(f"  {r['insider']} — {r['position']}")
            print(f"  ${r['value']:,.0f} @ avg ${r['avg_price']:.2f}")

        print(
            f"  Scores: Role={r['role_score']} | Conviction={r['conviction_score']} | "
            f"Timing={r['timing_score']} | Cluster={r['cluster_score']} | "
            f"Thesis={r['thesis_score']}"
        )
        if flags_str:
            print(flags_str)
        print(f"  {r['reasoning']}")
        print()
