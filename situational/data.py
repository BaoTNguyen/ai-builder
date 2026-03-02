"""
Market data fetching via yfinance.

Three responsibilities:
  get_underlying_data  — price, beta, dividend yield, sector
  get_option_chain     — filtered chain (near-the-money, nearest expiries)
  get_events           — earnings date, ex-dividend date, recent news

Risk-free rate is fetched from ^IRX (13-week T-bill) with a hardcoded
fallback — short-dated T-bill is more appropriate than the 10-year for
equity options with typical 30–90 DTE horizons.

Chain filtering keeps only strikes within ±strike_range_pct of spot and
the nearest max_expiries expiry dates. This caps what the agent sees to
a manageable subset and avoids flooding the context with hundreds of rows.
"""

from datetime import date, datetime
import yfinance as yf

_FALLBACK_RF = 0.043   # ~4.3% — update manually if rates shift significantly

SECTOR_ETF = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Consumer Cyclical":      "XLY",
    "Healthcare":             "XLV",
    "Energy":                 "XLE",
    "Communication Services": "XLC",
    "Consumer Defensive":     "XLP",
    "Industrials":            "XLI",
    "Basic Materials":        "XLB",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
}


def _spot(ticker_obj) -> float:
    info = ticker_obj.info
    price = (
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("navPrice")
    )
    if price:
        return float(price)
    # fast_info fallback (works for ETFs too)
    return float(ticker_obj.fast_info.get("last_price", 0))


def get_risk_free_rate() -> float:
    """13-week T-bill annualised yield, with hardcoded fallback."""
    try:
        irx = yf.Ticker("^IRX")
        rate = irx.fast_info.get("last_price")
        if rate and rate > 0:
            return round(float(rate) / 100, 5)
    except Exception:
        pass
    return _FALLBACK_RF


def get_underlying_data(ticker: str) -> dict:
    """
    Current price, beta, dividend yield, sector, and risk-free rate
    for a single underlying.
    """
    t    = yf.Ticker(ticker)
    info = t.info
    rf   = get_risk_free_rate()

    return {
        "ticker":        ticker.upper(),
        "price":         round(_spot(t), 4),
        "beta":          float(info.get("beta") or 1.0),
        "dividend_yield": float(info.get("dividendYield") or 0.0),
        "sector":        info.get("sector", "Unknown"),
        "industry":      info.get("industry", "Unknown"),
        "risk_free_rate": rf,
    }


def get_option_chain(
    ticker: str,
    max_dte: int = 365,
    strike_range_pct: float = 0.15,
) -> dict:
    """
    Filtered option chain for all expiries within max_dte calendar days.

    Only strikes within ±strike_range_pct of current spot are included.
    Returns bid, ask, IV, volume, open interest, and ITM flag per contract.
    Days to expiry is pre-calculated for each expiry bucket.
    """
    t     = yf.Ticker(ticker)
    spot  = _spot(t)
    lo    = spot * (1 - strike_range_pct)
    hi    = spot * (1 + strike_range_pct)
    today = date.today()

    cols   = ["strike", "bid", "ask", "impliedVolatility", "volume", "openInterest", "inTheMoney"]
    rename = {"impliedVolatility": "iv", "openInterest": "oi", "inTheMoney": "itm"}

    chain_out = {}
    eligible  = [
        exp for exp in t.options
        if (datetime.strptime(exp, "%Y-%m-%d").date() - today).days <= max_dte
    ]
    for expiry in eligible:
        try:
            raw    = t.option_chain(expiry)
            dte    = (datetime.strptime(expiry, "%Y-%m-%d").date() - today).days

            calls  = (
                raw.calls[cols]
                .query("@lo <= strike <= @hi")
                .rename(columns=rename)
                .round({"iv": 4})
                .to_dict("records")
            )
            puts   = (
                raw.puts[cols]
                .query("@lo <= strike <= @hi")
                .rename(columns=rename)
                .round({"iv": 4})
                .to_dict("records")
            )

            chain_out[expiry] = {
                "days_to_expiry": dte,
                "calls": calls,
                "puts":  puts,
            }
        except Exception:
            continue

    return {
        "ticker":        ticker.upper(),
        "current_price": round(spot, 4),
        "strike_range":  {"low": round(lo, 4), "high": round(hi, 4)},
        "chain":         chain_out,
    }


def get_events(ticker: str) -> dict:
    """
    Upcoming events that affect option pricing for a given ticker.

    Returns:
        earnings:      date, days_away, EPS estimate if available
        ex_dividend:   date, days_away, dividend amount
        recent_news:   last 5 headline titles (for sector/event context)

    All fields are optional — missing data is omitted rather than erroring.
    """
    t    = yf.Ticker(ticker)
    info = t.info
    events: dict = {}

    # Earnings ────────────────────────────────────────────────────────
    try:
        cal = t.calendar
        if cal is not None:
            ed = (
                cal.get("Earnings Date")
                or (cal.iloc[0] if hasattr(cal, "iloc") else None)
            )
            if ed is not None:
                if hasattr(ed, "__iter__") and not isinstance(ed, str):
                    ed = list(ed)[0]
                if hasattr(ed, "date"):
                    ed = ed.date()
                elif isinstance(ed, str):
                    ed = datetime.strptime(ed[:10], "%Y-%m-%d").date()
                days_away = (ed - date.today()).days
                events["earnings"] = {
                    "date":      str(ed),
                    "days_away": days_away,
                    "eps_estimate": cal.get("Earnings Average"),
                }
    except Exception:
        pass

    # Ex-dividend ─────────────────────────────────────────────────────
    try:
        ts = info.get("exDividendDate")
        if ts:
            ex_date   = date.fromtimestamp(int(ts))
            days_away = (ex_date - date.today()).days
            events["ex_dividend"] = {
                "date":      str(ex_date),
                "days_away": days_away,
                "amount":    info.get("lastDividendValue"),
            }
    except Exception:
        pass

    # Recent news headlines ────────────────────────────────────────────
    try:
        raw_news = t.news or []
        headlines = []
        for item in raw_news[:5]:
            # yfinance ≥0.2.x wraps content inside a "content" key
            content = item.get("content") or item
            title   = content.get("title") or content.get("headline", "")
            pub     = content.get("pubDate") or content.get("providerPublishTime", "")
            if title:
                headlines.append({"title": title, "published": str(pub)[:10]})
        if headlines:
            events["recent_news"] = headlines
    except Exception:
        pass

    return {"ticker": ticker.upper(), "events": events}
