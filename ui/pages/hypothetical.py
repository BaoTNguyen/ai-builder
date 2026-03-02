"""
Position Builder (formerly Hypothetical).

Left column:  option chain browser → full chain table → click row to add.
Right column: per-position analysis (scenario chart + P&L table) in expanders.
Bottom:       portfolio Greeks bar (option positions only) + events panel.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import streamlit as st

from situational.tools import dispatch
from situational.agent import run_position_analysis_agent, run_stack_analysis_agent
from portfolio.positions import PORTFOLIO
from ui.components.charts import pnl_decomp_table, scenario_chart
from ui.components.metrics import greeks_bar

# ── Ticker universe ───────────────────────────────────────────────────────────

TICKERS = [
    # Portfolio holdings
    "SPY", "QQQ", "IWM", "SCHD",
    "NVDA", "TSLA", "META", "AMZN", "MSFT", "SHOP", "PLTR", "SOFI", "BAC", "WFC",
    # Large-cap popular for options
    "AAPL", "GOOGL", "GOOG", "NFLX", "DIS", "UBER", "V", "MA", "JPM", "GS", "C",
    # High-IV / retail favourites
    "AMD", "ARM", "MU", "INTC", "SMCI", "AVGO",
    "MARA", "RIOT", "COIN", "HOOD",
    "GME", "AMC",
    "RIVN", "F", "NIO", "LCID",
    "SNAP", "ROKU", "PINS",
    "SQ", "PYPL",
    "XOM", "CVX", "OXY",
    # Broad ETFs used for hedging / plays
    "GLD", "SLV", "TLT", "HYG", "XLK", "XLF", "XLE",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _equity_positions() -> list[dict]:
    live = st.session_state.get("live_prices", {})
    return [
        {
            "position_type": "equity",
            "ticker":        item["ticker"],
            "shares":        item["shares"],
        }
        for item in PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
    ]


def _all_existing_positions() -> list[dict]:
    equity  = _equity_positions()
    options = [
        {
            "ticker":      p["ticker"],
            "option_type": p["option_type"],
            "strike":      p["strike"],
            "expiry":      p["expiry"],
            "contracts":   p["contracts"],
            "sigma":       p["sigma"],
        }
        for p in st.session_state.hyp_positions
    ]
    return equity + options


@st.cache_data(ttl=180, show_spinner=False)
def _load_chain(ticker: str, max_dte: int, strike_range_pct: float) -> dict:
    return json.loads(dispatch("get_option_chain", {
        "ticker":           ticker.upper(),
        "max_dte":          max_dte,
        "strike_range_pct": strike_range_pct,
    }))


@st.cache_data(ttl=300, show_spinner=False)
def _load_events(ticker: str) -> dict:
    return json.loads(dispatch("get_events", {"ticker": ticker.upper()}))


def _insight_key(ticker: str, option_type: str, strike: float, expiry: str, contracts: int) -> str:
    """Stable string key for the per-position Agent 1 insight cache."""
    return f"{ticker}|{option_type}|{strike}|{expiry}|{contracts}"


def _cached_insight(key: str) -> str | None:
    return st.session_state.get("_insight_cache", {}).get(key)


def _store_insight(key: str, insight: str) -> None:
    if "_insight_cache" not in st.session_state:
        st.session_state["_insight_cache"] = {}
    st.session_state["_insight_cache"][key] = insight


def _run_analysis(
    option_type: str, ticker: str, strike: float, expiry: str,
    contracts: int, entry_price: float, sigma: float,
) -> dict:
    return json.loads(dispatch("calculate_position_analysis", {
        "option_type":  option_type,
        "ticker":       ticker,
        "strike":       strike,
        "expiry":       expiry,
        "contracts":    contracts,
        "entry_price":  entry_price,
        "sigma":        sigma,
        "days_forward": 0,
    }))


def _portfolio_greeks(positions: list[dict]) -> dict | None:
    if not positions:
        return None
    result = json.loads(dispatch("get_portfolio_greeks", {
        "positions": [
            {
                "ticker":      p["ticker"],
                "option_type": p["option_type"],
                "strike":      p["strike"],
                "expiry":      p["expiry"],
                "contracts":   p["contracts"],
                "sigma":       p["sigma"],
            }
            for p in positions
        ]
    }))
    return result.get("summary")


def _dte(expiry: str) -> int:
    return (datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days


def _expiry_label(expiry: str) -> str:
    dte = _dte(expiry)
    try:
        d = datetime.strptime(expiry, "%Y-%m-%d")
        month_str = d.strftime("%b %d, %Y")
    except Exception:
        month_str = expiry
    return f"{month_str}  ({dte} DTE)"


def _nan_float(val, default: float = 0.0) -> float:
    """Return default if val is None or NaN, else float(val)."""
    try:
        f = float(val)
        return default if f != f else f  # f != f is True only for NaN
    except (TypeError, ValueError):
        return default


def _fmt_int(val) -> str:
    """Format an integer field, returning '—' if NaN or missing."""
    try:
        f = float(val)
        if f != f:  # NaN
            return "—"
        return str(int(f))
    except (TypeError, ValueError):
        return "—"


def _chain_df(contracts: list[dict], spot: float) -> pd.DataFrame:
    """Build a display dataframe from the raw chain contracts list."""
    rows = []
    for c in contracts:
        bid = _nan_float(c.get("bid"))
        ask = _nan_float(c.get("ask"))
        mid = round((bid + ask) / 2, 3) if (bid or ask) else 0.0
        rows.append({
            "Strike":  c["strike"],
            "Bid":     bid,
            "Ask":     ask,
            "Mid":     mid,
            "IV":      f"{_nan_float(c.get('iv')):.1%}",
            "Volume":  _fmt_int(c.get("volume")),
            "OI":      _fmt_int(c.get("oi")),
            "ITM":     "✓" if c.get("itm") else "",
            "_iv_raw": _nan_float(c.get("iv")),
            "_mid":    mid,
        })
    return pd.DataFrame(rows)


# ── Chain browser ─────────────────────────────────────────────────────────────

def _chain_browser() -> dict | None:
    """
    Renders ticker search, expiry selector, full call/put chain tables.
    Returns a position dict when the user confirms adding a contract, else None.
    """
    st.subheader("Option chain")

    # Searchable ticker dropdown
    ticker = st.selectbox(
        "Ticker",
        options=TICKERS,
        index=TICKERS.index("SOFI"),
        placeholder="Search ticker…",
    )

    col_load, col_range = st.columns([2, 3])
    with col_range:
        strike_range_pct = st.select_slider(
            "Strike range",
            options=[0.10, 0.15, 0.20, 0.30, 0.40, 0.50],
            value=0.30,
            format_func=lambda v: f"±{int(v*100)}% of spot",
        )
    with col_load:
        load = st.button("Load chain", type="primary", use_container_width=True)

    chain_key = f"chain_{ticker}_{strike_range_pct}"
    if load:
        with st.spinner(f"Loading {ticker} chain…"):
            chain_data = _load_chain(ticker, max_dte=365, strike_range_pct=strike_range_pct)
        st.session_state[chain_key] = chain_data

    if chain_key not in st.session_state:
        return None

    chain_data = st.session_state[chain_key]
    if "error" in chain_data:
        st.error(f"Could not load chain: {chain_data['error']}")
        return None

    chain   = chain_data.get("chain", {})
    spot    = chain_data.get("current_price", 0)
    expiries = list(chain.keys())

    if not expiries:
        st.warning("No option chain available for this ticker.")
        return None

    st.caption(f"Live price: **${spot:.2f}**")

    selected_expiry = st.selectbox(
        "Expiry",
        options=expiries,
        format_func=_expiry_label,
    )
    if not selected_expiry:
        return None

    option_type = st.radio("Type", ["Call", "Put"], horizontal=True)
    side        = "calls" if option_type == "Call" else "puts"
    contracts   = chain[selected_expiry].get(side, [])

    if not contracts:
        st.caption("No contracts in range for this expiry.")
        return None

    df = _chain_df(contracts, spot)
    display_cols = ["Strike", "Bid", "Ask", "Mid", "IV", "Volume", "OI", "ITM"]

    st.caption("Click a row to select a contract.")
    event = st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"chain_table_{ticker}_{selected_expiry}_{side}",
    )

    selected_rows = event.selection.rows if event.selection else []
    if not selected_rows:
        return None

    row    = df.iloc[selected_rows[0]]
    strike = float(row["Strike"])
    iv_raw = float(row["_iv_raw"])
    mid    = float(row["_mid"])
    itm    = row["ITM"] == "✓"

    # ── Contract form ─────────────────────────────────────────────────────────
    st.divider()
    st.write(
        f"Selected: {ticker} {selected_expiry} "
        f"${strike:.2f} {option_type.upper()} — "
        f"Mid ${mid:.3f} — IV {iv_raw:.1%} — "
        f"{'ITM' if itm else 'OTM'}"
    )

    n_contracts = st.number_input(
        "Contracts  (1 contract = 100 shares)",
        min_value=1, max_value=500, value=1, step=1,
        key=f"ncon_{ticker}_{selected_expiry}_{strike}_{side}",
    )

    if st.button(
        f"Add {n_contracts}× {ticker} ${strike:.2f} {option_type.upper()} — {_expiry_label(selected_expiry)}",
        type="primary",
        use_container_width=True,
        key=f"add_{ticker}_{selected_expiry}_{strike}_{side}",
    ):
        return {
            "ticker":      ticker,
            "option_type": option_type.lower(),
            "strike":      strike,
            "expiry":      selected_expiry,
            "contracts":   n_contracts,
            "entry_price": mid if mid > 0 else 0.01,
            "sigma":       iv_raw if iv_raw > 0 else 0.30,
        }

    return None


# ── Position card ─────────────────────────────────────────────────────────────

def _position_card(pos: dict, idx: int) -> None:
    dte  = _dte(pos["expiry"])
    analyzed_keys: set = st.session_state.get("_analyzed_position_keys", set())
    pos_key = (pos["ticker"], pos["option_type"], pos["strike"], pos["expiry"])
    new_tag = "  · NEW" if pos_key not in analyzed_keys else ""
    label = (
        f"{pos['contracts']}× {pos['ticker']}  "
        f"{pos['expiry']}  ${pos['strike']}  "
        f"{pos['option_type'].upper()}  —  {dte} DTE"
        f"{new_tag}"
    )

    with st.expander(label, expanded=(idx == 0)):
        if st.button("Remove", key=f"remove_{idx}"):
            st.session_state.hyp_positions.pop(idx)
            # Removing a position invalidates the last analyzed set so remaining
            # positions correctly re-trigger Agent 2 on next "Analyse" click.
            st.session_state.pop("_analyzed_position_keys", None)
            st.rerun()

        insight = pos.get("insight")
        if insight:
            st.info(insight.replace("$", r"\$"))

        analysis = pos.get("analysis")
        if not analysis:
            st.caption("Analysis unavailable.")
            return

        greeks = analysis.get("greeks", {})
        st.markdown("#### Greeks")

        investor_level = (
            st.session_state.get("investor_profile", {}).get("level", "intermediate")
            if st.session_state.get("investor_profile") else "intermediate"
        )

        if investor_level == "beginner":
            st.info(
                f"This position loses **${abs(greeks.get('theta_per_day', 0)):.2f}/day** "
                f"to time decay, and gains/loses **${abs(greeks.get('vega_per_pct', 0)):.2f}** "
                f"per 1% change in implied volatility.",
                icon="ℹ️",
            )
        else:
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Delta",        f"{greeks.get('delta', 0):+.4f}",
                      help="Change in option price per $1 move in the underlying.")
            g2.metric("Gamma",        f"{greeks.get('gamma', 0):.6f}",
                      help="Rate of change of delta per $1 move. Highest ATM near expiry.")
            g3.metric("Theta / day",  f"${greeks.get('theta_per_day', 0):+.2f}",
                      help="Daily time decay in dollars for this position.")
            g4.metric("Vega / 1% IV", f"${greeks.get('vega_per_pct', 0):+.2f}",
                      help="P&L change per 1 percentage-point move in implied volatility.")
            und = analysis.get("underlying", {})
            st.caption(
                f"Underlying: ${und.get('price', 0):.2f}  ·  "
                f"Beta: {und.get('beta', 1.0):.2f}  ·  "
                f"Sector: {und.get('sector', '—')}"
            )

        st.markdown("#### Scenario Analysis")
        if analysis.get("scenario_grid"):
            st.plotly_chart(
                scenario_chart(analysis["scenario_grid"], ticker=pos["ticker"]),
                use_container_width=True,
                key=f"schart_{idx}",
            )

        st.markdown(
            "#### P&L Decomposition",
            help=(
                "How much of P&L at each price move comes from each Greek. "
                "Directional (delta) = stock movement. "
                "Convexity (gamma) = acceleration effect. "
                "Time decay (theta) = cost of holding. "
                "Volatility (vega) = IV change impact."
            ),
        )
        if analysis.get("pnl_decomposition"):
            pnl_decomp_table(analysis["pnl_decomposition"])



# ── Events panel ──────────────────────────────────────────────────────────────

def _events_panel(tickers: list[str]) -> None:
    if not tickers:
        return

    st.markdown("### Events & Catalysts")
    st.caption(
        "High-signal events that meaningfully affect option pricing: "
        "earnings (IV expansion → crush), ex-dividend (put pricing), sector news."
    )

    cols = st.columns(min(len(tickers), 3))
    for i, ticker in enumerate(tickers):
        with cols[i % len(cols)]:
            with st.spinner(f"Loading {ticker} events…"):
                data   = _load_events(ticker)
            events = data.get("events", {})

            st.markdown(f"**{ticker}**")

            earnings = events.get("earnings")
            if earnings:
                days  = earnings.get("days_away", "?")
                edate = earnings.get("date", "")
                eps   = earnings.get("eps_estimate")
                color = "red" if isinstance(days, int) and days <= 14 else "orange"
                st.markdown(
                    f'<span style="color:{color};font-weight:600">'
                    f'📅 Earnings in {days} days ({edate})</span>',
                    unsafe_allow_html=True,
                )
                if eps:
                    st.caption(f"EPS estimate: ${eps:.2f}")
            else:
                st.caption("No upcoming earnings found.")

            ex_div = events.get("ex_dividend")
            if ex_div:
                st.caption(
                    f"💰 Ex-dividend: {ex_div.get('date')} "
                    f"({ex_div.get('days_away')} days) "
                    f"— ${ex_div.get('amount', 0):.4f}/share"
                )

            news = events.get("recent_news", [])
            if news:
                with st.expander("Recent news"):
                    for item in news[:3]:
                        st.markdown(f"- {item['title']} _{item['published']}_")


# ── Main page ─────────────────────────────────────────────────────────────────

def show() -> None:
    st.title("Position Builder")
    st.caption("Add option positions to see how they affect your portfolio before committing to a trade.")

    if "hyp_positions" not in st.session_state:
        st.session_state.hyp_positions = []

    positions = st.session_state.hyp_positions

    # ── Portfolio Greeks bar + stack analysis ────────────────────────────────
    if positions:
        with st.spinner("Computing option Greeks…"):
            summary = _portfolio_greeks(positions)
        if summary:
            greeks_bar(summary, label=f"Option Greeks — {len(positions)} position(s)")

        # Invalidate cached analysis when stack changes
        stack_hash = str([(p["ticker"], p["strike"], p["expiry"], p["option_type"], p["contracts"]) for p in positions])
        if st.session_state.get("_stack_hash") != stack_hash:
            st.session_state.pop("stack_analysis", None)
            st.session_state["_stack_hash"] = stack_hash

        # Count positions not included in the last Agent 2 run
        analyzed_keys: set = st.session_state.get("_analyzed_position_keys", set())
        new_count = sum(
            1 for p in positions
            if (p["ticker"], p["option_type"], p["strike"], p["expiry"]) not in analyzed_keys
        )
        btn_label = (
            "Analyse positions"
            if new_count == 0
            else f"Analyse positions  ·  {new_count} new"
        )

        col_btn, _ = st.columns([2, 5])
        with col_btn:
            if st.button(btn_label, type="primary", use_container_width=True):
                investor_lvl = (
                    st.session_state.get("investor_profile", {}).get("level", "intermediate")
                    if st.session_state.get("investor_profile") else "intermediate"
                )
                unique_tickers = list(dict.fromkeys(p["ticker"] for p in positions))
                events_by_ticker = {
                    t: _load_events(t).get("events", {}) for t in unique_tickers
                }
                with st.spinner("Analysing position stack…"):
                    st.session_state["stack_analysis"] = run_stack_analysis_agent(
                        positions=positions,
                        portfolio_summary=summary or {},
                        events_by_ticker=events_by_ticker,
                        investor_level=investor_lvl,
                        position_insights=[p.get("insight", "") for p in positions],
                    )
                # Record which positions were covered in this Agent 2 run
                st.session_state["_analyzed_position_keys"] = {
                    (p["ticker"], p["option_type"], p["strike"], p["expiry"])
                    for p in positions
                }
                st.rerun()

        if "stack_analysis" in st.session_state:
            with st.container(border=True):
                st.markdown(st.session_state["stack_analysis"])

        st.divider()
    else:
        st.info(
            "No additional positions yet. Add one below to see how it affects "
            "your Greeks, time decay, and volatility exposure.",
            icon="ℹ️",
        )
        st.divider()

    # ── Two-column layout ─────────────────────────────────────────────────────
    left, right = st.columns([4, 5], gap="large")

    with left:
        selected = _chain_browser()
        if selected:
            investor_lvl = (
                st.session_state.get("investor_profile", {}).get("level", "intermediate")
                if st.session_state.get("investor_profile") else "intermediate"
            )
            key = (selected["ticker"], selected["option_type"], selected["strike"], selected["expiry"])
            dup_idx = next(
                (i for i, p in enumerate(st.session_state.hyp_positions)
                 if (p["ticker"], p["option_type"], p["strike"], p["expiry"]) == key),
                None,
            )

            if dup_idx is not None:
                # Aggregate contracts onto existing entry, re-run analysis
                existing  = st.session_state.hyp_positions[dup_idx]
                new_total = existing["contracts"] + selected["contracts"]
                ikey      = _insight_key(
                    selected["ticker"], selected["option_type"],
                    selected["strike"], selected["expiry"], new_total,
                )
                cached = _cached_insight(ikey)
                with st.spinner(
                    f"Updating {selected['ticker']} position "
                    f"({existing['contracts']} → {new_total} contracts)"
                    + ("" if cached is None else " — insight from cache"),
                ):
                    analysis = _run_analysis(
                        option_type=selected["option_type"],
                        ticker=selected["ticker"],
                        strike=selected["strike"],
                        expiry=selected["expiry"],
                        contracts=new_total,
                        entry_price=existing["entry_price"],
                        sigma=selected["sigma"],
                    )
                    if "error" not in analysis:
                        if cached is None:
                            events_data = _load_events(selected["ticker"]).get("events", {})
                            cached = run_position_analysis_agent(
                                {**existing, "contracts": new_total, "analysis": analysis},
                                events_data,
                                investor_level=investor_lvl,
                            )
                            _store_insight(ikey, cached)
                        existing["contracts"] = new_total
                        existing["sigma"]     = selected["sigma"]
                        existing["analysis"]  = analysis
                        existing["insight"]   = cached
                        st.rerun()
                    else:
                        st.error(f"Analysis failed: {analysis['error']}")
            else:
                # New position — check insight cache before calling Agent 1
                ikey   = _insight_key(
                    selected["ticker"], selected["option_type"],
                    selected["strike"], selected["expiry"], selected["contracts"],
                )
                cached = _cached_insight(ikey)
                with st.spinner(
                    f"Analysing {selected['ticker']} position"
                    + ("" if cached is None else " — insight from cache"),
                ):
                    analysis = _run_analysis(**{k: selected[k] for k in [
                        "option_type", "ticker", "strike", "expiry",
                        "contracts", "entry_price", "sigma",
                    ]})
                    if "error" not in analysis:
                        if cached is None:
                            events_data = _load_events(selected["ticker"]).get("events", {})
                            cached = run_position_analysis_agent(
                                {**selected, "analysis": analysis},
                                events_data,
                                investor_level=investor_lvl,
                            )
                            _store_insight(ikey, cached)
                        selected["analysis"] = analysis
                        selected["insight"]  = cached
                        st.session_state.hyp_positions.append(selected)
                        st.rerun()
                    else:
                        st.error(f"Analysis failed: {analysis['error']}")

    with right:
        if not positions:
            st.caption("Added positions will appear here.")
        else:
            st.subheader(f"Stack — {len(positions)} position{'s' if len(positions) != 1 else ''}")
            for i, pos in enumerate(positions):
                _position_card(pos, i)

    # ── Events panel ──────────────────────────────────────────────────────────
    if positions:
        st.divider()
        _events_panel(list(dict.fromkeys(p["ticker"] for p in positions)))
