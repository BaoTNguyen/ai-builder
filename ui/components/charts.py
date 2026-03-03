"""
Reusable chart components for the hypothetical position builder.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def scenario_chart(scenario_grid: list, ticker: str = "") -> go.Figure:
    """
    Three-line scenario chart: underlying price move (%) vs P&L ($).
    One line per IV regime: crush, unchanged, expansion.
    """
    x = [row["price_move_pct"] for row in scenario_grid]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=x, y=[row["iv_expansion"] for row in scenario_grid],
        name="IV rises 30% (approaching event)",
        line=dict(color="#4CAF50", width=2, dash="dot"),
        mode="lines+markers",
        marker=dict(size=5),
    ))
    fig.add_trace(go.Scatter(
        x=x, y=[row["iv_unchanged"] for row in scenario_grid],
        name="IV holds",
        line=dict(color="#2196F3", width=2.5, dash="solid"),
        mode="lines+markers",
        marker=dict(size=5),
    ))
    fig.add_trace(go.Scatter(
        x=x, y=[row["iv_crush"] for row in scenario_grid],
        name="IV drops 30% (post-earnings)",
        line=dict(color="#F44336", width=2, dash="dash"),
        mode="lines+markers",
        marker=dict(size=5),
    ))

    fig.add_hline(y=0, line_color="rgba(128,128,128,0.5)", line_width=1)
    fig.add_vline(x=0, line_color="rgba(128,128,128,0.3)", line_dash="dot", line_width=1)

    title = f"Scenario P&L — {ticker}" if ticker else "Scenario P&L"
    fig.update_layout(
        title=dict(text=title, font=dict(size=14)),
        xaxis_title="Underlying price move (%)",
        yaxis_title="P&L ($)",
        xaxis=dict(ticksuffix="%", zeroline=False, gridcolor="rgba(128,128,128,0.1)"),
        yaxis=dict(tickprefix="$", gridcolor="rgba(128,128,128,0.1)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=11)),
        margin=dict(l=10, r=10, t=60, b=10),
        height=300,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def combined_payoff_chart(
    positions: list[dict],
    spot: float,
    ticker: str,
    strategy_label: str,
    equity_shares: int = 0,
    equity_entry: float = 0.0,
) -> go.Figure:
    """
    Exact-at-expiry payoff chart for a multi-leg options strategy.

    X-axis: underlying price at expiry.
    Y-axis: combined P&L in dollars.
    Green fill above breakeven, red fill below.

    positions must each have: option_type, strike, contracts, entry_price.
    equity_shares / equity_entry included when equity is part of the strategy.
    """
    prices = np.linspace(spot * 0.65, spot * 1.35, 300)

    pnls = []
    for S_i in prices:
        total = 0.0
        for pos in positions:
            mult = pos["contracts"] * 100
            if pos["option_type"] == "call":
                intrinsic = max(S_i - pos["strike"], 0.0)
            else:
                intrinsic = max(pos["strike"] - S_i, 0.0)
            total += (intrinsic - pos["entry_price"]) * mult
        if equity_shares and equity_entry:
            total += (S_i - equity_entry) * equity_shares
        pnls.append(total)

    # Split into positive / negative arrays for dual-colour fill
    pos_y = [max(v, 0.0) for v in pnls]
    neg_y = [min(v, 0.0) for v in pnls]

    fig = go.Figure()

    # Fills (rendered behind the main line)
    fig.add_trace(go.Scatter(
        x=prices, y=pos_y, fill="tozeroy",
        fillcolor="rgba(76,175,80,0.18)",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=prices, y=neg_y, fill="tozeroy",
        fillcolor="rgba(244,67,54,0.18)",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))

    # Main P&L line
    fig.add_trace(go.Scatter(
        x=prices, y=pnls,
        name="P&L at expiry",
        line=dict(color="#2196F3", width=2.5),
        mode="lines",
        hovertemplate="Price: $%{x:.2f}<br>P&L: $%{y:,.0f}<extra></extra>",
    ))

    # Current spot
    fig.add_vline(
        x=spot,
        line_color="rgba(128,128,128,0.6)", line_dash="dot", line_width=1.5,
        annotation_text=f"Spot ${spot:.2f}",
        annotation_position="top right",
        annotation_font_size=11,
    )

    # Strike lines
    strikes_seen: set = set()
    for pos in positions:
        k = pos["strike"]
        if k not in strikes_seen:
            fig.add_vline(
                x=k,
                line_color="rgba(255,193,7,0.5)", line_dash="dash", line_width=1,
                annotation_text=f"K={k:.0f}",
                annotation_position="bottom right",
                annotation_font_size=10,
            )
            strikes_seen.add(k)

    # Zero line
    fig.add_hline(y=0, line_color="rgba(128,128,128,0.4)", line_width=1)

    fig.update_layout(
        title=dict(
            text=f"Payoff at Expiry — {strategy_label} ({ticker})",
            font=dict(size=14),
        ),
        xaxis_title="Underlying price at expiry ($)",
        yaxis_title="P&L ($)",
        xaxis=dict(tickprefix="$", zeroline=False, gridcolor="rgba(128,128,128,0.1)"),
        yaxis=dict(tickprefix="$", gridcolor="rgba(128,128,128,0.1)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=11)),
        margin=dict(l=10, r=10, t=60, b=10),
        height=320,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def pnl_decomp_table(pnl_decomposition: dict) -> None:
    """
    Color-coded P&L decomposition: Greek rows × scenario columns.
    Green = profit, red = loss, intensity scales with magnitude.
    """
    row_labels = {
        "delta":        "Directional (delta)",
        "gamma":        "Convexity (gamma)",
        "theta":        "Time decay (theta)",
        "vega":         "Volatility (vega)",
        "total_approx": "Total (approx)",
        "total_exact":  "Total (exact)",
    }
    col_map = {
        "minus_5pct": "−5% move",
        "flat_0pct":  "Flat",
        "plus_5pct":  "+5% move",
    }

    data = {}
    for key, col_label in col_map.items():
        d = pnl_decomposition.get(key, {})
        data[col_label] = {
            label: d.get(field, 0.0)
            for field, label in row_labels.items()
        }

    df = pd.DataFrame(data)

    all_vals = [v for row in data.values() for v in row.values() if v != 0]
    max_abs = max(abs(v) for v in all_vals) if all_vals else 1.0

    def _color(val):
        if val > 0:
            alpha = min(abs(val) / max_abs, 1.0) * 0.55 + 0.1
            return f"background-color: rgba(76,175,80,{alpha:.2f}); color: white"
        elif val < 0:
            alpha = min(abs(val) / max_abs, 1.0) * 0.55 + 0.1
            return f"background-color: rgba(244,67,54,{alpha:.2f}); color: white"
        return "color: rgba(128,128,128,0.7)"

    styled = (
        df.style
        .map(_color)
        .format("${:+.2f}")
    )
    st.dataframe(styled, use_container_width=True)
