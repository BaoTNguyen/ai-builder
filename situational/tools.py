"""
Tool implementations for the situational portfolio construction agent.

Five tools:
  1. get_underlying_data      — price, beta, dividend yield, sector
  2. get_option_chain         — filtered chain (near-the-money, nearest expiries)
  3. calculate_position_analysis — Greeks + scenario grid + P&L decomposition
  4. get_events               — earnings, ex-dividend, recent news
  5. get_portfolio_greeks     — beta-weighted aggregate across option positions

Token efficiency strategy:
  - All computation (GBS, central difference, scenario P&L) happens here in Python.
    The agent receives structured numbers, not raw data to re-interpret.
  - Chain is pre-filtered to ±15% of spot and nearest 3 expiries.
  - Events are pre-structured — no raw article text reaches the agent.
  - get_portfolio_greeks is optional; only call it when portfolio context is needed.
"""

import json

import yfinance as yf

from situational.data   import get_underlying_data, get_option_chain, get_events
from situational.greeks import (
    calculate_greeks,
    run_scenario_analysis,
    pnl_decomposition,
    aggregate_portfolio_greeks,
    calculate_hypothetical_impact,
)


# ─── Implementations ──────────────────────────────────────────────────────────

def _calculate_position_analysis(
    option_type: str,
    ticker: str,
    strike: float,
    expiry: str,
    contracts: int,
    entry_price: float,
    sigma: float,
    days_forward: int = 0,
) -> dict:
    """
    Full analysis for a single option position.

    Fetches live underlying data (price, beta, dividend yield, risk-free rate),
    then runs Greeks + scenario grid + P&L decomposition.

    Returns everything the agent needs to narrate the position clearly.
    """
    from datetime import date, datetime
    underlying = get_underlying_data(ticker)
    S    = underlying["price"]
    r    = underlying["risk_free_rate"]
    q    = underlying["dividend_yield"]
    beta = underlying["beta"]

    today = date.today()
    T     = max(
        (datetime.strptime(expiry, "%Y-%m-%d").date() - today).days / 365,
        1e-8,
    )

    analysis = run_scenario_analysis(
        option_type, S, K=strike, T=T, r=r, q=q,
        sigma=sigma, contracts=contracts, entry_price=entry_price,
        days_forward=days_forward,
    )

    return {
        "ticker":     ticker.upper(),
        "option_type": option_type,
        "strike":     strike,
        "expiry":     expiry,
        "contracts":  contracts,
        "underlying": {"price": S, "beta": beta, "sector": underlying["sector"]},
        **analysis,
    }


def _get_portfolio_greeks(positions: list) -> dict:
    """
    Fetches live SPY price then aggregates beta-weighted Greeks.

    Each position must include: option_type, ticker, K (strike), expiry,
    contracts, sigma, and optionally beta.  Missing beta defaults to 1.0.
    """
    from datetime import date, datetime

    spy_price = float(yf.Ticker("SPY").fast_info["last_price"])

    enriched = []
    for pos in positions:
        und = get_underlying_data(pos["ticker"])
        today = date.today()
        T = max(
            (datetime.strptime(pos["expiry"], "%Y-%m-%d").date() - today).days / 365,
            1e-8,
        )
        enriched.append({
            **pos,
            "S":    und["price"],
            "K":    pos["strike"],
            "T":    T,
            "r":    und["risk_free_rate"],
            "q":    und["dividend_yield"],
            "beta": pos.get("beta") or und["beta"],
        })

    return aggregate_portfolio_greeks(enriched, spy_price)


# ─── Dispatch ─────────────────────────────────────────────────────────────────

def dispatch(name: str, tool_input: dict) -> str:
    try:
        if name == "get_underlying_data":
            result = get_underlying_data(tool_input["ticker"])

        elif name == "get_option_chain":
            result = get_option_chain(
                tool_input["ticker"],
                max_dte=tool_input.get("max_dte", 365),
                strike_range_pct=tool_input.get("strike_range_pct", 0.15),
            )

        elif name == "calculate_position_analysis":
            result = _calculate_position_analysis(
                option_type=tool_input["option_type"],
                ticker=tool_input["ticker"],
                strike=tool_input["strike"],
                expiry=tool_input["expiry"],
                contracts=tool_input["contracts"],
                entry_price=tool_input["entry_price"],
                sigma=tool_input["sigma"],
                days_forward=tool_input.get("days_forward", 0),
            )

        elif name == "get_events":
            result = get_events(tool_input["ticker"])

        elif name == "get_portfolio_greeks":
            result = _get_portfolio_greeks(tool_input["positions"])

        elif name == "calculate_hypothetical_impact":
            spy_price = float(yf.Ticker("SPY").fast_info["last_price"])

            # Enrich each position (existing + new) with live underlying data
            from datetime import date, datetime

            def _enrich(pos: dict) -> dict:
                und = get_underlying_data(pos["ticker"])
                if pos.get("position_type") == "equity":
                    return {
                        **pos,
                        "S":    und["price"],
                        "beta": pos.get("beta") or und["beta"],
                    }
                T = max(
                    (datetime.strptime(pos["expiry"], "%Y-%m-%d").date() - date.today()).days / 365,
                    1e-8,
                )
                return {
                    **pos,
                    "S":    und["price"],
                    "K":    pos["strike"],
                    "T":    T,
                    "r":    und["risk_free_rate"],
                    "q":    und["dividend_yield"],
                    "beta": pos.get("beta") or und["beta"],
                }

            existing  = [_enrich(p) for p in tool_input["existing_positions"]]
            new_pos   = _enrich(tool_input["new_position"])
            new_pos["expiry"] = tool_input["new_position"]["expiry"]

            result = calculate_hypothetical_impact(existing, new_pos, spy_price)

        elif name == "calculate_pnl_decomposition":
            und = get_underlying_data(tool_input["ticker"])
            from datetime import date, datetime
            T = max(
                (datetime.strptime(tool_input["expiry"], "%Y-%m-%d").date() - date.today()).days / 365,
                1e-8,
            )
            result = pnl_decomposition(
                option_type=tool_input["option_type"],
                S=und["price"],
                K=tool_input["strike"],
                T=T,
                r=und["risk_free_rate"],
                q=und["dividend_yield"],
                sigma=tool_input["sigma"],
                contracts=tool_input["contracts"],
                entry_price=tool_input["entry_price"],
                price_move=tool_input.get("price_move", 0.0),
                iv_change_abs=tool_input.get("iv_change_abs", 0.0),
                days_elapsed=tool_input.get("days_elapsed", 0),
            )

        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        result = {"error": str(e)}

    return json.dumps(result)


# ─── Schemas ──────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_underlying_data",
        "description": (
            "Fetch current price, beta, dividend yield, sector, and risk-free rate "
            "for a ticker. Call this first to get live market context before any "
            "options analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker symbol, e.g. 'SOFI'"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_option_chain",
        "description": (
            "Fetch the filtered option chain for a ticker: strikes within ±15% of spot, "
            "nearest 3 expiry dates. Returns bid, ask, IV, open interest, and ITM flag. "
            "Use this to show available contracts — not to do analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":           {"type": "string"},
                "max_dte":          {"type": "integer", "description": "Maximum days to expiry to include (default 365 — excludes LEAPS)."},
                "strike_range_pct": {"type": "number",  "description": "Strike filter radius as decimal (default 0.15 = ±15%)."},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "calculate_position_analysis",
        "description": (
            "Full analysis for a single option position: live underlying data, "
            "central-difference Greeks (delta, gamma, theta/day, vega/1%), "
            "a scenario grid (price ×3 IV regimes), and P&L decomposition by Greek "
            "at flat/±5% moves. All dollar values are for the total position. "
            "Use sigma from the selected contract's IV field."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "option_type": {"type": "string", "enum": ["call", "put"]},
                "ticker":      {"type": "string"},
                "strike":      {"type": "number", "description": "Strike price."},
                "expiry":      {"type": "string", "description": "Expiry date as YYYY-MM-DD."},
                "contracts":   {"type": "integer", "description": "Number of contracts (each = 100 shares)."},
                "entry_price": {"type": "number", "description": "Price per share paid/received when position was opened. Use current mid-price for new positions."},
                "sigma":       {"type": "number", "description": "Implied volatility as decimal (e.g. 0.65 for 65%)."},
                "days_forward": {"type": "integer", "description": "Project scenarios this many days forward (default 0 = today)."},
            },
            "required": ["option_type", "ticker", "strike", "expiry", "contracts", "entry_price", "sigma"],
        },
    },
    {
        "name": "get_events",
        "description": (
            "Upcoming events for a ticker: earnings date (+ days away + EPS estimate), "
            "ex-dividend date, and last 5 news headlines. "
            "Call this after get_underlying_data to understand what's on the calendar "
            "before narrating position risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_portfolio_greeks",
        "description": (
            "Beta-weighted portfolio-level Greeks across multiple option positions. "
            "Beta-weights delta and gamma against SPY (cross-asset comparison). "
            "Theta and vega are raw dollar sums. "
            "Only call this when analysing portfolio-wide impact, not for single-position analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "positions": {
                    "type": "array",
                    "description": "List of open option positions.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker":      {"type": "string"},
                            "option_type": {"type": "string", "enum": ["call", "put"]},
                            "strike":      {"type": "number"},
                            "expiry":      {"type": "string"},
                            "contracts":   {"type": "integer"},
                            "sigma":       {"type": "number"},
                            "beta":        {"type": "number", "description": "Override beta (fetched automatically if omitted)."},
                        },
                        "required": ["ticker", "option_type", "strike", "expiry", "contracts", "sigma"],
                    },
                },
            },
            "required": ["positions"],
        },
    },
    {
        "name": "calculate_hypothetical_impact",
        "description": (
            "Shows how adding a new option position shifts portfolio-level beta-weighted Greeks. "
            "Computes before/after/change for beta-weighted delta, beta-weighted gamma, "
            "total theta, and total vega. Use this when the user has existing positions "
            "and wants to understand the portfolio impact of a potential new trade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "existing_positions": {
                    "type": "array",
                    "description": (
                        "Current holdings — stocks, ETFs, or options. "
                        "Set position_type='equity' with a shares field for stocks/ETFs; "
                        "omit position_type (or set 'option') for option positions."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "position_type": {"type": "string", "enum": ["equity", "option"], "description": "Defaults to 'option' if omitted."},
                            "ticker":        {"type": "string"},
                            "shares":        {"type": "number", "description": "Required for equity positions."},
                            "option_type":   {"type": "string", "enum": ["call", "put"], "description": "Required for option positions."},
                            "strike":        {"type": "number", "description": "Required for option positions."},
                            "expiry":        {"type": "string", "description": "Required for option positions."},
                            "contracts":     {"type": "integer", "description": "Required for option positions."},
                            "sigma":         {"type": "number", "description": "Required for option positions."},
                            "beta":          {"type": "number", "description": "Override beta (fetched automatically if omitted)."},
                        },
                        "required": ["ticker"],
                    },
                },
                "new_position": {
                    "type": "object",
                    "description": "The hypothetical position being evaluated.",
                    "properties": {
                        "ticker":      {"type": "string"},
                        "option_type": {"type": "string", "enum": ["call", "put"]},
                        "strike":      {"type": "number"},
                        "expiry":      {"type": "string"},
                        "contracts":   {"type": "integer"},
                        "sigma":       {"type": "number"},
                        "beta":        {"type": "number", "description": "Override beta (fetched automatically if omitted)."},
                    },
                    "required": ["ticker", "option_type", "strike", "expiry", "contracts", "sigma"],
                },
            },
            "required": ["existing_positions", "new_position"],
        },
    },
    {
        "name": "calculate_pnl_decomposition",
        "description": (
            "P&L breakdown by Greek for a specific scenario (actual or hypothetical move). "
            "Shows how much of total P&L came from delta, gamma, theta, and vega separately. "
            "Use this when narrating why a position gained or lost value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "option_type":   {"type": "string", "enum": ["call", "put"]},
                "ticker":        {"type": "string"},
                "strike":        {"type": "number"},
                "expiry":        {"type": "string"},
                "contracts":     {"type": "integer"},
                "entry_price":   {"type": "number"},
                "sigma":         {"type": "number"},
                "price_move":    {"type": "number", "description": "$ change in underlying since entry (e.g. +1.50)."},
                "iv_change_abs": {"type": "number", "description": "Absolute IV change in decimal (e.g. -0.10 for IV falling 10 vol pts)."},
                "days_elapsed":  {"type": "integer", "description": "Calendar days since position was opened."},
            },
            "required": ["option_type", "ticker", "strike", "expiry", "contracts", "entry_price", "sigma"],
        },
    },
]
