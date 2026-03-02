"""
Portfolio Greeks summary bar — reused on the hypothetical page.
"""

import streamlit as st

_HELP = {
    "bw_delta": (
        "Beta-weighted delta: your portfolio's SPY-equivalent directional exposure. "
        "Positive = net long (profits when market rises). "
        "Negative = net short (profits when market falls)."
    ),
    "bw_gamma": (
        "Beta-weighted gamma: how fast your directional exposure (BW-delta) changes "
        "per $1 move in SPY. High gamma means your delta accelerates quickly — "
        "large moves have outsized effects."
    ),
    "theta": (
        "Theta: dollars lost per calendar day from time decay across all option "
        "positions. This is the cost of holding options. "
        "Equity-only portfolios have no time decay."
    ),
    "vega": (
        "Vega: dollars gained (or lost) per 1 percentage-point increase in implied "
        "volatility. Positive = long volatility (benefits from IV spikes). "
        "Negative = short volatility (benefits from IV compression)."
    ),
}


def greeks_bar(summary: dict, label: str = "Option position Greeks") -> None:
    """
    Render a one-row st.metric summary of portfolio-level beta-weighted Greeks.

    Args:
        summary: dict with keys beta_weighted_delta, beta_weighted_gamma,
                 total_theta_per_day, total_vega_per_pct
        label:   caption shown above the bar
    """
    bw_delta = summary.get("beta_weighted_delta", 0.0)
    bw_gamma = summary.get("beta_weighted_gamma", 0.0)
    theta    = summary.get("total_theta_per_day", 0.0)
    vega     = summary.get("total_vega_per_pct", 0.0)

    st.caption(label)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BW Delta",     f"{bw_delta:+.3f}",  help=_HELP["bw_delta"])
    c2.metric("BW Gamma",     f"{bw_gamma:+.5f}",  help=_HELP["bw_gamma"])
    c3.metric("Theta / day",  f"${theta:+.2f}",    help=_HELP["theta"])
    c4.metric("Vega / 1% IV", f"${vega:+.2f}",     help=_HELP["vega"])
