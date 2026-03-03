"""
Portfolio dashboard: current holdings with live prices and market values.
"""

import json

import streamlit as st
import yfinance as yf
import pandas as pd

from portfolio.positions import PORTFOLIO
from situational.tools import dispatch as _events_dispatch


def _load_portfolio_plan() -> dict | None:
    try:
        with open("profiles/portfolio_plan.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_events(ticker: str) -> dict:
    return json.loads(_events_dispatch("get_events", {"ticker": ticker.upper()}))


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_prices(tickers: tuple) -> dict:
    prices = {}
    for ticker in tickers:
        try:
            t     = yf.Ticker(ticker)
            info  = t.info
            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or float(t.fast_info.get("last_price", 0))
            )
            prices[ticker] = round(float(price), 2)
        except Exception:
            prices[ticker] = None
    return prices


def _holdings_table(items: list, live: dict) -> pd.DataFrame:
    rows = []
    for item in items:
        ticker  = item["ticker"]
        live_p  = live.get(ticker)
        book_p  = item["price"]
        shares  = item["shares"]
        book_mv = book_p * shares
        live_mv = (live_p * shares) if live_p else None
        pnl     = (live_mv - book_mv) if live_mv else None
        pnl_pct = (pnl / book_mv * 100) if pnl is not None else None

        rows.append({
            "Ticker":         ticker,
            "Name":           item["name"],
            "Shares":         shares,
            "Book price":     f"${book_p:.2f}",
            "Live price":     f"${live_p:.2f}" if live_p else "—",
            "Market value":   f"${live_mv:,.0f}" if live_mv else f"${book_mv:,.0f}",
            "Unrealised P&L": f"${pnl:+,.0f} ({pnl_pct:+.1f}%)" if pnl is not None else "—",
        })
    return pd.DataFrame(rows)


def show() -> None:
    st.title("Portfolio Dashboard")

    all_items  = PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
    tickers    = tuple(item["ticker"] for item in all_items)
    etf_tickers = {item["ticker"] for item in PORTFOLIO["etfs"]}

    with st.spinner("Fetching live prices…"):
        live = _fetch_prices(tickers)

    st.session_state.live_prices = live

    # ── Summary metrics ───────────────────────────────────────────────────────
    total_book = sum(item["price"] * item["shares"] for item in all_items)
    total_live = sum(
        live[item["ticker"]] * item["shares"]
        for item in all_items
        if live.get(item["ticker"])
    )
    total_pnl     = total_live - total_book
    total_pnl_pct = total_pnl / total_book * 100

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total book value",   f"${total_book:,.0f}")
    c2.metric("Total market value", f"${total_live:,.0f}")
    c3.metric("Unrealised P&L",     f"${total_pnl:+,.0f}", delta=f"{total_pnl_pct:+.1f}%")
    c4.metric("Positions",          f"{len(all_items)} holdings")

    st.divider()

    # ── ETFs ──────────────────────────────────────────────────────────────────
    st.subheader("ETFs")
    st.dataframe(_holdings_table(PORTFOLIO["etfs"], live), use_container_width=True, hide_index=True)

    # ── Stocks ────────────────────────────────────────────────────────────────
    st.subheader("Stocks")
    st.dataframe(_holdings_table(PORTFOLIO["stocks"], live), use_container_width=True, hide_index=True)

    st.divider()

    if st.button("→ Add more positions", type="primary"):
        st.switch_page(st.session_state["_pages"]["hypothetical"])

    # ── Resilience Strategies ─────────────────────────────────────────────────
    _MECHANIC = {
        "covered_call":     "Sell the right to buy your shares above a chosen price; keep the premium if they aren't called away",
        "protective_put":   "Buy the right to sell your shares at a set price; limits how far the position can fall",
        "cash_secured_put": "Commit cash to buy more shares at a lower price if assigned; collect premium while waiting",
    }

    plan = _load_portfolio_plan()
    if plan:
        st.divider()

        # Event alerts
        alert_tickers: list[tuple] = []
        for item in PORTFOLIO["stocks"]:
            t = item["ticker"]
            try:
                data     = _fetch_events(t)
                earnings = data.get("events", {}).get("earnings")
                if earnings:
                    days = earnings.get("days_away")
                    if isinstance(days, int) and days <= 14:
                        alert_tickers.append((t, days, earnings.get("date", "")))
            except Exception:
                pass

        if alert_tickers:
            for ticker, days, edate in sorted(alert_tickers, key=lambda x: x[1]):
                st.warning(
                    f"**{ticker}** earnings in **{days} day{'s' if days != 1 else ''}** ({edate}) "
                    f"— review any options on this ticker before expiry.",
                    icon="⚠️",
                )

        with st.container(border=True):
            st.subheader("Resilience Strategies for Your Portfolio")
            st.caption(
                "Options strategies your portfolio qualifies for based on share counts and "
                "your investor level. Whether any of these make sense depends on market "
                "conditions at the time — use the position builder to explore specific trades. "
                "Not financial advice."
            )

            recs = plan.get("strategy_recommendations", {})

            cc_items    = [r for r in recs.get("income", [])      if r["ticker"] not in etf_tickers]
            put_items   = [r for r in recs.get("protection", [])  if r["ticker"] not in etf_tickers]
            csp_items   = [r for r in recs.get("accumulation", []) if r["ticker"] not in etf_tickers]

            cc_contracts = sum(r.get("contracts", 1) for r in cc_items)

            c1, c2, c3 = st.columns(3)
            c1.metric(
                "Covered call contracts",
                cc_contracts,
                help="Total contracts available across eligible positions (100 shares per contract).",
            )
            c2.metric(
                "Positions to protect",
                len(put_items),
                help="Stock positions that qualify for protective puts based on role and concentration.",
            )
            c3.metric(
                "CSP candidates",
                len(csp_items),
                help="Positions approaching the 100-share covered call threshold via cash-secured puts.",
            )

            rows = []
            for item in cc_items:
                rows.append({
                    "Ticker":          item["ticker"],
                    "Strategy":        "Covered Call",
                    "Contracts":       item.get("contracts", "—"),
                    "Eligibility":     item.get("eligibility_note", "—"),
                    "What it involves": _MECHANIC["covered_call"],
                })
            for item in put_items:
                rows.append({
                    "Ticker":          item["ticker"],
                    "Strategy":        "Protective Put",
                    "Contracts":       "—",
                    "Eligibility":     item.get("eligibility_note", "—"),
                    "What it involves": _MECHANIC["protective_put"],
                })
            for item in csp_items:
                rows.append({
                    "Ticker":          item["ticker"],
                    "Strategy":        "Cash-Secured Put",
                    "Contracts":       "—",
                    "Eligibility":     item.get("eligibility_note", "—"),
                    "What it involves": _MECHANIC["cash_secured_put"],
                })

            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            lvl       = plan.get("investor_level", "")
            generated = plan.get("generated_at", "")
            st.caption(
                f"Portfolio agent · {lvl.capitalize()} investor profile"
                + (f" · {generated[:10]}" if generated else "")
            )
