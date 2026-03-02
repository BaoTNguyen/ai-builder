"""
Portfolio dashboard: current holdings with live prices and market values.
"""

import streamlit as st
import yfinance as yf
import pandas as pd

from portfolio.positions import PORTFOLIO


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

    all_items = PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
    tickers   = tuple(item["ticker"] for item in all_items)

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

    if st.button("→ Explore hypothetical positions", type="primary"):
        st.switch_page(st.session_state["_pages"]["hypothetical"])
