"""
Portfolio data.

Portfolio: $200k total
  $120k across 4 ETFs ($30k each)
  $80k across 10 stocks in two tiers:
    - "hold_growth" tier  ($6k each × 5): expensive flagship names, no CC intent
    - "income_value" tier ($10k each × 5): priced accessibly enough for 100+ shares,
      making covered calls viable across most positions in this tier

100-share constraint at $200k:
  ETFs:  SCHD ($84 × 357 shares → 3 contracts), IWM ($215 × 139 shares → 1 contract)
         SPY and QQQ remain below 100 shares even at $30k — held as anchors anyway.

  Stocks (hold_growth tier, $6k each):
    NVDA $132 → 45 shares, TSLA $330 → 18, META $665 → 9, SHOP $120 → 50, AMZN $242 → 24
    None reach 100 shares — intentional, these are held for capital appreciation not income.

  Stocks (income_value tier, $10k each):
    PLTR $97 → 103 shares → 1 contract  (was 52 shares / 0 contracts at $5k)
    BAC  $45 → 222 shares → 2 contracts (was 112 / 1)
    SOFI $15 → 666 shares → 6 contracts (was 336 / 3)
    WFC  $75 → 133 shares → 1 contract  (was 67 / 0 — newly unlocked)
    MSFT $412 → 24 shares → 0 contracts (expensive defensive growth, held for appreciation)

  Total covered call contracts: SCHD(3) + IWM(1) + PLTR(1) + BAC(2) + SOFI(6) + WFC(1) = 14

Position roles (used by portfolio agent to recommend strategy):
  anchor:          SPY, QQQ — core buy-and-hold; options only for protection
  income_etf:      SCHD — 3 covered call contracts; primary ETF income engine
  smallcap_value:  IWM — 1 covered call contract now unlocked; also protective puts
  hold_growth:     NVDA, TSLA, META, SHOP, AMZN — protective puts, opportunistic calls
  income_value:    MSFT, PLTR, BAC, SOFI, WFC — covered calls where eligible + CSPs

Strategy gates and level ordering have moved to core/gates.py.
"""

PORTFOLIO: dict = {
    "total_value": 200_000,
    "etfs": [
        {
            "ticker": "SPY",
            "name": "SPDR S&P 500 ETF",
            "theme": "Broad U.S. large-cap — core market exposure",
            "role": "anchor",
            "shares": 50,
            "price": 600.00,
            "market_value": 30_000.00,
            "contracts_available": 0,
        },
        {
            "ticker": "QQQ",
            "name": "Invesco Nasdaq-100 ETF",
            "theme": "Tech-heavy Nasdaq-100 growth",
            "role": "anchor",
            "shares": 57,
            "price": 520.00,
            "market_value": 29_640.00,
            "contracts_available": 0,
        },
        {
            "ticker": "SCHD",
            "name": "Schwab U.S. Dividend Equity ETF",
            "theme": "High-quality U.S. dividend payers — income anchor",
            "role": "income_etf",
            "shares": 357,
            "price": 84.00,
            "market_value": 29_988.00,
            "contracts_available": 3,   # 357 shares → 3 × 100-share contracts
        },
        {
            "ticker": "IWM",
            "name": "iShares Russell 2000 ETF",
            "theme": "U.S. small-cap diversification",
            "role": "smallcap_value",
            "shares": 139,
            "price": 215.00,
            "market_value": 29_885.00,
            "contracts_available": 1,   # 139 shares → 1 contract (newly unlocked at $30k)
        },
    ],
    "stocks": [
        # ── Hold-for-growth tier ($6k each, ~$30k total) ──────────────────────
        # Flagship growth names: expensive per share, held for capital appreciation.
        # No covered call intent — capping upside conflicts with the holding thesis.
        # Best suited for protective puts (downside protection) and opportunistic
        # short-dated calls when conviction is high.
        {
            "ticker": "NVDA",
            "name": "NVIDIA Corporation",
            "theme": "AI infrastructure / semiconductors",
            "role": "hold_growth",
            "tier": "hold_growth",
            "shares": 45,
            "price": 132.00,
            "market_value": 5_940.00,
            "contracts_available": 0,
        },
        {
            "ticker": "TSLA",
            "name": "Tesla Inc.",
            "theme": "EV / energy / autonomous driving",
            "role": "hold_growth",
            "tier": "hold_growth",
            "shares": 18,
            "price": 330.00,
            "market_value": 5_940.00,
            "contracts_available": 0,
        },
        {
            "ticker": "META",
            "name": "Meta Platforms Inc.",
            "theme": "Social media / AI / advertising platform",
            "role": "hold_growth",
            "tier": "hold_growth",
            "shares": 9,
            "price": 665.00,
            "market_value": 5_985.00,
            "contracts_available": 0,
        },
        {
            "ticker": "SHOP",
            "name": "Shopify Inc.",
            "theme": "E-commerce infrastructure (Canadian)",
            "role": "hold_growth",
            "tier": "hold_growth",
            "shares": 50,
            "price": 120.00,
            "market_value": 6_000.00,
            "contracts_available": 0,
        },
        {
            "ticker": "AMZN",
            "name": "Amazon.com Inc.",
            "theme": "Cloud (AWS) / e-commerce / advertising",
            "role": "hold_growth",
            "tier": "hold_growth",
            "shares": 24,
            "price": 242.00,
            "market_value": 5_808.00,
            "contracts_available": 0,
        },
        # ── Income/value tier ($10k each, ~$50k total) ────────────────────────
        # At $10k per position, four of five names now clear the 100-share threshold.
        # MSFT remains below 100 shares (expensive at $412) — held as defensive growth.
        # PLTR and WFC are newly CC-eligible at this allocation.
        {
            "ticker": "MSFT",
            "name": "Microsoft Corporation",
            "theme": "Cloud (Azure) / AI / enterprise — defensive growth",
            "role": "income_value",
            "tier": "income_value",
            "shares": 24,
            "price": 412.00,
            "market_value": 9_888.00,
            "contracts_available": 0,
        },
        {
            "ticker": "PLTR",
            "name": "Palantir Technologies Inc.",
            "theme": "AI / government + commercial data analytics",
            "role": "income_value",
            "tier": "income_value",
            "shares": 103,
            "price": 97.00,
            "market_value": 9_991.00,
            "contracts_available": 1,   # newly unlocked at $10k allocation
        },
        {
            "ticker": "BAC",
            "name": "Bank of America Corp.",
            "theme": "Diversified banking — U.S. economic bellwether (value)",
            "role": "income_value",
            "tier": "income_value",
            "shares": 222,
            "price": 45.00,
            "market_value": 9_990.00,
            "contracts_available": 2,   # 222 shares → 2 contracts
        },
        {
            "ticker": "SOFI",
            "name": "SoFi Technologies Inc.",
            "theme": "Fintech banking / lending platform (growth)",
            "role": "income_value",
            "tier": "income_value",
            "shares": 666,
            "price": 15.00,
            "market_value": 9_990.00,
            "contracts_available": 6,   # 666 shares → 6 contracts
        },
        {
            "ticker": "WFC",
            "name": "Wells Fargo & Co.",
            "theme": "U.S. retail / commercial banking (value)",
            "role": "income_value",
            "tier": "income_value",
            "shares": 133,
            "price": 75.00,
            "market_value": 9_975.00,
            "contracts_available": 1,   # newly unlocked at $10k allocation
        },
    ],
}

