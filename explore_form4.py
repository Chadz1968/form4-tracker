from edgar import set_identity, get_filings

set_identity("Mark Chadwick chadwick_mark@hotmail.com")

MIN_PURCHASE_VALUE = 50_000

INSIDER_ROLES = ["ceo", "cfo", "president", "chairman", "director",
                 "officer", "vp", "vice president", "chief"]

# One representative date per seasonal window (year, quarter, filing_date)
SAMPLE_WINDOWS = [
    (2025, 1, "2025-01-28"),   # late January
    (2025, 2, "2025-04-28"),   # late April
    (2025, 3, "2025-07-28"),   # late July
    (2025, 4, "2025-10-28"),   # late October
]

total_found   = 0
total_checked = 0
total_errors  = 0

for year, quarter, date in SAMPLE_WINDOWS:
    print(f"\n{'='*60}")
    print(f"Fetching Form 4 filings for {date} (Q{quarter} {year})...")

    filings = get_filings(year=year, quarter=quarter, form="4",
                          filing_date=date)

    print(f"Scanning for open-market purchases over ${MIN_PURCHASE_VALUE:,}...\n")

    found   = 0
    checked = 0
    errors  = 0
    seen    = set()

    for filing in filings:
        checked += 1

        try:
            accession = getattr(filing, "accession_no", None)
            if accession in seen:
                continue
            seen.add(accession)

            form4 = filing.obj()

            market_trades = getattr(form4, "market_trades", None)
            if market_trades is None or len(market_trades) == 0:
                continue

            p_trades = market_trades[market_trades["Code"] == "P"]
            if len(p_trades) == 0:
                continue

            insider  = getattr(form4, "insider_name", "?")
            position = getattr(form4, "position", "") or ""

            position_lower = position.lower()
            is_insider = any(role in position_lower for role in INSIDER_ROLES)
            is_institution = "/" in insider
            if not is_insider or is_institution:
                continue

            issuer       = getattr(form4, "issuer", None)
            company_name = getattr(issuer, "name", "?") if issuer else "?"
            ticker       = getattr(issuer, "ticker", "?") if issuer else "?"
            period       = getattr(form4, "reporting_period", "?")

            total_value = 0
            for _, row in p_trades.iterrows():
                try:
                    total_value += float(row["Shares"]) * float(row["Price"])
                except (TypeError, ValueError):
                    pass

            if total_value < MIN_PURCHASE_VALUE:
                continue

            print(f"*** QUALIFYING PURCHASE ***")
            print(f"  Company:  {company_name} ({ticker})")
            print(f"  Insider:  {insider} — {position}")
            print(f"  Date:     {period}")
            print(f"  Total $:  ${total_value:,.2f}")

            for _, row in p_trades.iterrows():
                print(f"  Trade:    {row['Shares']:,.0f} shares @ ${row['Price']}")

            print(f"  ---")
            found += 1

        except Exception:
            errors += 1
            continue

    print(f"\n[{date}] Checked {checked} filings | Found {found} qualifying | "
          f"Errors {errors}")
    total_found   += found
    total_checked += checked
    total_errors  += errors

print(f"\n{'='*60}")
print(f"TOTALS across all sample windows:")
print(f"  Checked: {total_checked}")
print(f"  Found:   {total_found} qualifying purchases")
print(f"  Errors:  {total_errors}")
