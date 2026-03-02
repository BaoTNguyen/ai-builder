"""
Reusable chart components for the hypothetical position builder.
"""

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
