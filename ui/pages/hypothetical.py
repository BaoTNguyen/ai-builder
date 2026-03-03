"""
Position Builder (formerly Hypothetical).

Left column:  option chain browser → full chain table → click row to add.
Right column: per-position analysis (scenario chart + P&L table) in expanders.
Bottom:       portfolio Greeks bar (option positions only) + events panel.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime

import numpy as np
import pandas as pd
import streamlit as st

from situational.tools import dispatch
from situational.agent import (
    run_position_analysis_agent,
    run_portfolio_impact_agent,
    run_stack_analysis_agent,
    run_strategy_analysis_agent,
)
from portfolio.positions import PORTFOLIO
from ui.components.charts import combined_payoff_chart, pnl_decomp_table, scenario_chart
from ui.components.metrics import greeks_bar


@st.cache_data(show_spinner=False)
def _load_portfolio_plan() -> dict:
    try:
        with open("profiles/portfolio_plan.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


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


def _full_portfolio_greeks(options: list[dict]) -> dict | None:
    """Beta-weighted Greeks for the full book: equity holdings + options overlay."""
    equity = [
        {"position_type": "equity", "ticker": item["ticker"], "shares": item["shares"]}
        for item in PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
    ]
    option_rows = [
        {
            "ticker":      p["ticker"],
            "option_type": p["option_type"],
            "strike":      p["strike"],
            "expiry":      p["expiry"],
            "contracts":   p["contracts"],
            "sigma":       p["sigma"],
        }
        for p in options
    ]
    result = json.loads(dispatch("get_portfolio_greeks", {"positions": equity + option_rows}))
    return result.get("summary")


def _equity_summary() -> list[dict]:
    """Compact equity context for the portfolio impact agent."""
    live = st.session_state.get("live_prices", {})
    total_live = sum(
        (live.get(item["ticker"]) or item["price"]) * item["shares"]
        for item in PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
    )
    rows = []
    for item in PORTFOLIO["etfs"] + PORTFOLIO["stocks"]:
        price = live.get(item["ticker"]) or item["price"]
        mv    = price * item["shares"]
        rows.append({
            "ticker":       item["ticker"],
            "shares":       item["shares"],
            "book_price":   item["price"],
            "market_value": round(mv, 0),
            "weight_pct":   round(mv / total_live * 100, 2) if total_live else 0,
            "role":         item.get("role", ""),
        })
    return rows


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


# ── Strategy helpers ──────────────────────────────────────────────────────────

# Flat lookup: ticker → portfolio item (shares + book price)
_EQUITY_MAP: dict = {
    item["ticker"]: item
    for item in PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
}


def _strategy_label(options: list[dict], has_equity: bool) -> str:
    """Identify common multi-leg strategies by leg structure."""
    calls  = [p for p in options if p["option_type"] == "call"]
    puts   = [p for p in options if p["option_type"] == "put"]
    longs  = [p for p in options if p["contracts"] > 0]
    shorts = [p for p in options if p["contracts"] < 0]
    n = len(options)

    # Single option against portfolio equity
    if has_equity and n == 1:
        if shorts and calls:
            return "Covered Call"
        if longs and puts:
            return "Protective Put"
        if longs and calls:
            return "Long Call (on held stock)"
        if shorts and puts:
            return "Cash-Secured Put"

    # Two-leg options-only strategies
    if n == 2 and len(calls) == 2 and len(longs) == 1 and len(shorts) == 1:
        long_k  = next(p["strike"] for p in calls if p["contracts"] > 0)
        short_k = next(p["strike"] for p in calls if p["contracts"] < 0)
        return "Bull Call Spread" if long_k < short_k else "Bear Call Spread"

    if n == 2 and len(puts) == 2 and len(longs) == 1 and len(shorts) == 1:
        long_k  = next(p["strike"] for p in puts if p["contracts"] > 0)
        short_k = next(p["strike"] for p in puts if p["contracts"] < 0)
        return "Bear Put Spread" if long_k > short_k else "Bull Put Spread"

    if n == 2 and len(calls) == 1 and len(puts) == 1:
        same_k      = calls[0]["strike"] == puts[0]["strike"]
        both_long   = len(longs) == 2
        both_short  = len(shorts) == 2
        if both_long:
            return "Long Straddle" if same_k else "Long Strangle"
        if both_short:
            return "Short Straddle" if same_k else "Short Strangle"
        # Risk reversal: long call + short put or vice versa
        if len(longs) == 1 and len(shorts) == 1:
            return "Risk Reversal"

    # Three-leg
    if n == 3 and len(calls) == 3:
        by_k = sorted(options, key=lambda p: p["strike"])
        signs = [1 if p["contracts"] > 0 else -1 for p in by_k]
        if signs == [1, -2, 1] or signs == [1, -1, 1]:
            return "Call Butterfly"

    if n == 3 and len(puts) == 3:
        by_k = sorted(options, key=lambda p: p["strike"])
        signs = [1 if p["contracts"] > 0 else -1 for p in by_k]
        if signs == [1, -1, 1] or signs == [-1, 2, -1]:
            return "Put Butterfly"

    return f"{n}-Leg Strategy"


def _sum_decomp(positions: list[dict]) -> dict:
    """Sum pnl_decomposition across all legs at each scenario."""
    combined: dict = {}
    for scenario_key in ("minus_5pct", "flat_0pct", "plus_5pct"):
        combined[scenario_key] = {}
        for field in ("delta", "gamma", "theta", "vega", "total_approx", "total_exact"):
            combined[scenario_key][field] = sum(
                pos["analysis"]["pnl_decomposition"].get(scenario_key, {}).get(field, 0.0)
                for pos in positions
                if pos.get("analysis") and pos["analysis"].get("pnl_decomposition")
            )
    return combined


def _payoff_stats(
    group: list[dict],
    spot: float,
    equity_shares: int = 0,
    equity_entry: float = 0.0,
) -> dict:
    """Compute max profit, max loss, breakevens, and net cost from the payoff grid."""
    prices = np.linspace(spot * 0.65, spot * 1.35, 300)
    pnls = []
    for S_i in prices:
        total = 0.0
        for pos in group:
            mult = pos["contracts"] * 100
            if pos["option_type"] == "call":
                intrinsic = max(S_i - pos["strike"], 0.0)
            else:
                intrinsic = max(pos["strike"] - S_i, 0.0)
            total += (intrinsic - pos["entry_price"]) * mult
        if equity_shares and equity_entry:
            total += (S_i - equity_entry) * equity_shares
        pnls.append(total)

    max_profit = max(pnls)
    max_loss   = min(pnls)

    # Breakeven: prices where P&L crosses zero (sign change between adjacent points)
    breakevens = []
    for i in range(len(pnls) - 1):
        if pnls[i] * pnls[i + 1] <= 0 and abs(pnls[i] - pnls[i + 1]) > 0:
            # Linear interpolation
            be = prices[i] + (prices[i + 1] - prices[i]) * (-pnls[i] / (pnls[i + 1] - pnls[i]))
            breakevens.append(round(float(be), 2))

    # Net cost: sum of premiums paid/received (positive = debit, negative = credit)
    net_cost = sum(
        pos["entry_price"] * pos["contracts"] * 100
        for pos in group
    )

    return {
        "max_profit":  round(float(max_profit), 2),
        "max_loss":    round(float(max_loss), 2),
        "breakevens":  breakevens,
        "net_cost":    round(net_cost, 2),
    }


def _strategy_cache_key(ticker: str, expiry: str, group: list[dict]) -> str:
    """Stable cache key for a strategy group's AI analysis."""
    legs = tuple(sorted(
        (p["option_type"], p["strike"], p["contracts"])
        for p in group
    ))
    return f"{ticker}|{expiry}|{legs}"


def _strategy_card(
    ticker: str,
    expiry: str,
    group: list[dict],
    equity_item: dict | None,
) -> None:
    """
    Combined payoff chart + P&L decomp for a multi-leg same-expiry group.
    Individual position cards still show Greeks + insight separately.
    """
    has_equity    = equity_item is not None
    label         = _strategy_label(group, has_equity)
    equity_shares = equity_item["shares"] if has_equity else 0
    equity_entry  = equity_item["price"]  if has_equity else 0.0

    # Derive spot from first position's analysis (all same ticker)
    spot = 0.0
    for pos in group:
        if pos.get("analysis"):
            spot = pos["analysis"].get("underlying", {}).get("price", 0.0)
            if spot:
                break

    if not spot:
        return  # can't draw chart without a spot price

    st.markdown(f"#### Strategy: {label} — {ticker} · {_expiry_label(expiry)}")

    if has_equity:
        st.caption(
            f"Includes {equity_shares:,}× {ticker} shares at book price "
            f"\\${equity_entry:.2f}/share from portfolio."
        )

    st.plotly_chart(
        combined_payoff_chart(
            positions=group,
            spot=spot,
            ticker=ticker,
            strategy_label=label,
            equity_shares=equity_shares,
            equity_entry=equity_entry,
        ),
        use_container_width=True,
        key=f"strat_{ticker}_{expiry}",
    )

    # Combined P&L decomp if all legs have analysis data
    if all(pos.get("analysis", {}).get("pnl_decomposition") for pos in group):
        st.markdown(
            "**Combined P&L decomposition**",
            help=(
                "Sum of each Greek's P&L contribution across all legs. "
                "Opposing legs partially cancel — e.g. a spread's net delta "
                "is smaller than either leg alone."
            ),
        )
        pnl_decomp_table(_sum_decomp(group))

    # ── Per-strategy AI analysis button ──────────────────────────────────────
    skey        = _strategy_cache_key(ticker, expiry, group)
    cache_store = st.session_state.setdefault("_strategy_analysis", {})
    cached_sa   = cache_store.get(skey)

    col_btn, _ = st.columns([2, 5])
    with col_btn:
        if st.button(
            "Analyse strategy",
            type="secondary",
            use_container_width=True,
            key=f"sa_btn_{ticker}_{expiry}",
        ):
            investor_lvl = (
                st.session_state.get("investor_profile", {}).get("level", "intermediate")
                if st.session_state.get("investor_profile") else "intermediate"
            )
            n_legs = len(group)
            with st.spinner(
                f"Analysing {n_legs} leg{'s' if n_legs != 1 else ''} "
                f"then synthesising {label}…"
            ):
                # Agent 1: run for any leg without a cached insight
                events_data = _load_events(ticker).get("events", {})
                for pos in group:
                    ikey = _insight_key(
                        pos["ticker"], pos["option_type"],
                        pos["strike"], pos["expiry"], pos["contracts"],
                    )
                    leg_insight = _cached_insight(ikey)
                    if leg_insight is None:
                        leg_insight = run_position_analysis_agent(
                            pos, events_data.get("events", events_data),
                            investor_level=investor_lvl,
                        )
                        _store_insight(ikey, leg_insight)
                    pos["insight"] = leg_insight

                # Agent 2: strategy synthesis
                stats      = _payoff_stats(group, spot, equity_shares, equity_entry)
                net_greeks = _sum_decomp(group)
                cached_sa  = run_strategy_analysis_agent(
                    strategy_label    = label,
                    group             = group,
                    net_greeks        = net_greeks,
                    payoff_stats      = stats,
                    events            = events_data,
                    investor_level    = investor_lvl,
                    position_insights = [p.get("insight", "") for p in group],
                    equity_shares     = equity_shares,
                    equity_entry      = equity_entry,
                )
            cache_store[skey] = cached_sa
            st.rerun()

    if cached_sa:
        parts     = [p.strip() for p in cached_sa.split("•") if p.strip()]
        formatted = "\n\n".join(f"• {p}" for p in parts).replace("$", r"\$")
        st.info(formatted)


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

    col_dir, col_n = st.columns([1, 2])
    with col_dir:
        direction = st.radio(
            "Direction",
            ["Buy", "Sell"],
            horizontal=True,
            key=f"dir_{ticker}_{selected_expiry}_{strike}_{side}",
        )
    with col_n:
        n_contracts = st.number_input(
            "Contracts  (1 = 100 shares)",
            min_value=1, max_value=500, value=1, step=1,
            key=f"ncon_{ticker}_{selected_expiry}_{strike}_{side}",
        )

    signed = n_contracts if direction == "Buy" else -n_contracts

    if st.button(
        f"{direction.upper()} {n_contracts}× {ticker} ${strike:.2f} {option_type.upper()} — {_expiry_label(selected_expiry)}",
        type="primary",
        use_container_width=True,
        key=f"add_{ticker}_{selected_expiry}_{strike}_{side}",
    ):
        return {
            "ticker":      ticker,
            "option_type": option_type.lower(),
            "strike":      strike,
            "expiry":      selected_expiry,
            "contracts":   signed,
            "entry_price": mid if mid > 0 else 0.01,
            "sigma":       iv_raw if iv_raw > 0 else 0.30,
        }

    return None


# ── Position card ─────────────────────────────────────────────────────────────

def _position_card(pos: dict, idx: int, hide_charts: bool = False) -> None:
    dte       = _dte(pos["expiry"])
    contracts = pos["contracts"]
    direction = "BUY" if contracts > 0 else "SELL"
    analyzed_keys: set = st.session_state.get("_analyzed_position_keys", set())
    pos_key = (pos["ticker"], pos["option_type"], pos["strike"], pos["expiry"])
    new_tag = "  · NEW" if pos_key not in analyzed_keys else ""
    label = (
        f"{direction} {abs(contracts)}× {pos['ticker']}  "
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
            parts = [p.strip() for p in insight.split("•") if p.strip()]
            formatted = "\n\n".join(f"• {p}" for p in parts).replace("$", r"\$")
            st.info(formatted)
        elif not hide_charts:
            # Standalone position — offer lazy per-position analysis
            if st.button("Analyse position", key=f"analyse_pos_{idx}", type="secondary"):
                investor_lvl = (
                    st.session_state.get("investor_profile", {}).get("level", "intermediate")
                    if st.session_state.get("investor_profile") else "intermediate"
                )
                ikey = _insight_key(
                    pos["ticker"], pos["option_type"],
                    pos["strike"], pos["expiry"], pos["contracts"],
                )
                cached = _cached_insight(ikey)
                with st.spinner(f"Analysing {pos['ticker']} position…"):
                    if cached is None:
                        events_data = _load_events(pos["ticker"]).get("events", {})
                        cached = run_position_analysis_agent(
                            pos, events_data, investor_level=investor_lvl,
                        )
                        _store_insight(ikey, cached)
                pos["insight"] = cached
                st.rerun()

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
            theta     = abs(greeks.get("theta_per_day", 0))
            theta_30  = abs(greeks.get("theta_at_30dte", theta))
            vega      = abs(greeks.get("vega_per_pct", 0))
            dte       = _dte(pos["expiry"])
            if dte > 30 and theta_30 > theta * 1.05:
                decay_text = (
                    f"Currently loses **\\${theta:.2f}/day** to time decay, "
                    f"accelerating to ~**\\${theta_30:.2f}/day** in the final 30 days before expiry."
                )
            else:
                decay_text = f"Loses **\\${theta:.2f}/day** to time decay."
            st.info(
                f"{decay_text} "
                f"Gains/loses **\\${vega:.2f}** per 1% change in implied volatility.",
                icon="ℹ️",
            )
        else:
            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Delta",        f"{greeks.get('delta', 0):+.4f}",
                      help="Change in option price per $1 move in the underlying.")
            g2.metric("Gamma",        f"{greeks.get('gamma', 0):.6f}",
                      help="Rate of change of delta per $1 move. Highest ATM near expiry.")
            theta_30 = greeks.get("theta_at_30dte")
            theta_help = (
                "Daily time decay in dollars at current DTE. "
                + (f"Accelerates to ${theta_30:+.2f}/day at 30 DTE." if theta_30 else "")
            )
            g3.metric("Theta / day",  f"${greeks.get('theta_per_day', 0):+.2f}",
                      help=theta_help)
            g4.metric("Vega / 1% IV", f"${greeks.get('vega_per_pct', 0):+.2f}",
                      help="P&L change per 1 percentage-point move in implied volatility.")
            und = analysis.get("underlying", {})
            st.caption(
                f"Underlying: ${und.get('price', 0):.2f}  ·  "
                f"Beta: {und.get('beta', 1.0):.2f}  ·  "
                f"Sector: {und.get('sector', '—')}"
            )

        if not hide_charts:
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
        else:
            st.caption("Scenario chart and P&L decomposition shown in the strategy view above.")



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
                missing = [p for p in positions if not p.get("insight")]
                with st.spinner(
                    f"Analysing {len(missing)} position{'s' if len(missing) != 1 else ''}, "
                    "then synthesising stack…"
                    if missing else "Synthesising position stack…"
                ):
                    # Ensure every position has an Agent 1 insight
                    for pos in missing:
                        ikey = _insight_key(
                            pos["ticker"], pos["option_type"],
                            pos["strike"], pos["expiry"], pos["contracts"],
                        )
                        leg_insight = _cached_insight(ikey)
                        if leg_insight is None:
                            leg_insight = run_position_analysis_agent(
                                pos,
                                events_by_ticker.get(pos["ticker"], {}),
                                investor_level=investor_lvl,
                            )
                            _store_insight(ikey, leg_insight)
                        pos["insight"] = leg_insight

                    st.session_state["stack_analysis"] = run_stack_analysis_agent(
                        positions=positions,
                        portfolio_summary=summary or {},
                        events_by_ticker=events_by_ticker,
                        investor_level=investor_lvl,
                        position_insights=[p.get("insight", "") for p in positions],
                    )
                st.session_state["_analyzed_position_keys"] = {
                    (p["ticker"], p["option_type"], p["strike"], p["expiry"])
                    for p in positions
                }
                st.rerun()

        if "stack_analysis" in st.session_state:
            with st.container(border=True):
                import re
                raw = st.session_state["stack_analysis"]
                # Flatten headings (##/###) to bold so font size stays consistent
                raw = re.sub(r"^#{1,6}\s+(.+)$", r"**\1**", raw, flags=re.MULTILINE)
                # Escape dollar signs to prevent LaTeX math mode
                raw = raw.replace("$", r"\$")
                st.markdown(raw)

        # ── Portfolio impact analysis ─────────────────────────────────────
        # Invalidate when positions change (same hash as stack)
        pi_hash = st.session_state.get("_stack_hash", "")
        if st.session_state.get("_pi_hash") != pi_hash:
            st.session_state.pop("portfolio_impact", None)
            st.session_state["_pi_hash"] = pi_hash

        col_pi, _ = st.columns([3, 4])
        with col_pi:
            if st.button(
                "Analyse portfolio impact",
                type="primary",
                use_container_width=True,
                key="pi_btn",
            ):
                investor_lvl = (
                    st.session_state.get("investor_profile", {}).get("level", "intermediate")
                    if st.session_state.get("investor_profile") else "intermediate"
                )
                unique_tickers = list(dict.fromkeys(p["ticker"] for p in positions))
                events_by_ticker = {
                    t: _load_events(t).get("events", {}) for t in unique_tickers
                }
                missing = [p for p in positions if not p.get("insight")]
                with st.spinner("Computing full portfolio impact…"):
                    # Ensure all positions have Agent 1 insights
                    for pos in missing:
                        ikey = _insight_key(
                            pos["ticker"], pos["option_type"],
                            pos["strike"], pos["expiry"], pos["contracts"],
                        )
                        leg_insight = _cached_insight(ikey)
                        if leg_insight is None:
                            leg_insight = run_position_analysis_agent(
                                pos,
                                events_by_ticker.get(pos["ticker"], {}),
                                investor_level=investor_lvl,
                            )
                            _store_insight(ikey, leg_insight)
                        pos["insight"] = leg_insight

                    # Full portfolio Greeks (equity + options)
                    full_greeks = _full_portfolio_greeks(positions)

                    # Portfolio market value for theta % calculation
                    live        = st.session_state.get("live_prices", {})
                    total_mv    = sum(
                        (live.get(item["ticker"]) or item["price"]) * item["shares"]
                        for item in PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
                    )

                    st.session_state["portfolio_impact"] = run_portfolio_impact_agent(
                        equity_holdings       = _equity_summary(),
                        options_positions     = positions,
                        options_greeks        = summary or {},
                        full_portfolio_greeks = full_greeks or {},
                        total_portfolio_value = total_mv,
                        events_by_ticker      = events_by_ticker,
                        investor_level        = investor_lvl,
                        strategy_insights     = st.session_state.get("_strategy_analysis"),
                    )
                st.rerun()

        if "portfolio_impact" in st.session_state:
            with st.container(border=True):
                st.markdown("##### Portfolio Impact")
                parts     = [p.strip() for p in st.session_state["portfolio_impact"].split("•") if p.strip()]
                formatted = "\n\n".join(f"• {p}" for p in parts).replace("$", r"\$")
                st.markdown(formatted)
            st.warning(
                "**AI analysis complete.** The analysis above is AI's responsibility. "
                "Order entry, timing, and position sizing remain with you — these depend on "
                "real-time market conditions, tax considerations, and personal conviction "
                "that this system cannot access.",
                icon="⚠️",
            )

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
            sel_dir = "buy" if selected["contracts"] > 0 else "sell"
            key = (selected["ticker"], selected["option_type"], selected["strike"], selected["expiry"], sel_dir)
            dup_idx = next(
                (i for i, p in enumerate(st.session_state.hyp_positions)
                 if (p["ticker"], p["option_type"], p["strike"], p["expiry"],
                     "buy" if p["contracts"] > 0 else "sell") == key),
                None,
            )

            if dup_idx is not None:
                # Aggregate contracts onto existing entry, re-run quantitative analysis
                existing  = st.session_state.hyp_positions[dup_idx]
                new_total = existing["contracts"] + selected["contracts"]
                dir_label = "BUY" if new_total > 0 else "SELL"
                with st.spinner(
                    f"Updating {selected['ticker']} {dir_label} position "
                    f"({abs(existing['contracts'])} → {abs(new_total)} contracts)…"
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
                    existing["contracts"] = new_total
                    existing["sigma"]     = selected["sigma"]
                    existing["analysis"]  = analysis
                    existing.pop("insight", None)   # stale — re-analysed on next click
                    st.rerun()
                else:
                    st.error(f"Analysis failed: {analysis['error']}")
            else:
                # New position — quantitative analysis only; AI deferred to button
                with st.spinner(f"Loading {selected['ticker']} position…"):
                    analysis = _run_analysis(**{k: selected[k] for k in [
                        "option_type", "ticker", "strike", "expiry",
                        "contracts", "entry_price", "sigma",
                    ]})
                if "error" not in analysis:
                    selected["analysis"] = analysis
                    st.session_state.hyp_positions.append(selected)
                    st.rerun()
                else:
                    st.error(f"Analysis failed: {analysis['error']}")

    with right:
        if not positions:
            st.caption("Added positions will appear here.")
        else:
            st.subheader(f"Stack — {len(positions)} position{'s' if len(positions) != 1 else ''}")

            # ── Group by (ticker, expiry) for strategy views ──────────────
            by_group: dict[tuple, list] = defaultdict(list)
            for pos in positions:
                by_group[(pos["ticker"], pos["expiry"])].append(pos)

            # Walk positions in insertion order; render each group exactly
            # once — strategy card + its leg cards together — then a divider
            # between groups. Standalone positions follow with no dividers.
            rendered_groups: set = set()
            standalone_queue: list = []  # (original_index, pos)

            for i, pos in enumerate(positions):
                gkey   = (pos["ticker"], pos["expiry"])
                group  = by_group[gkey]
                equity = _EQUITY_MAP.get(pos["ticker"])
                is_multi = len(group) >= 2 or (len(group) == 1 and equity)

                if is_multi:
                    if gkey not in rendered_groups:
                        # Strategy card (chart + decomp + analyse button)
                        _strategy_card(pos["ticker"], pos["expiry"], group, equity)
                        # Leg cards directly underneath — no divider between them
                        for leg in group:
                            leg_idx = positions.index(leg)
                            _position_card(leg, leg_idx, hide_charts=True)
                        st.divider()
                        rendered_groups.add(gkey)
                else:
                    standalone_queue.append((i, pos))

            # Standalone positions at the end, no dividers
            for i, pos in standalone_queue:
                _position_card(pos, i, hide_charts=False)

    # ── Events panel ──────────────────────────────────────────────────────────
    if positions:
        st.divider()
        _events_panel(list(dict.fromkeys(p["ticker"] for p in positions)))
