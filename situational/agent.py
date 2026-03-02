"""
Situational portfolio construction agent.

Analyses a specific option position (existing or potential) in context:
  — live Greeks and theoretical pricing via GBS + central difference
  — scenario grid across price moves and IV regimes
  — P&L decomposition by Greek variable
  — upcoming events that affect the position
  — optional portfolio-level beta-weighted impact

Workflow (3–4 tool calls, situation-dependent):
  1. get_underlying_data          — live price, beta, div yield, sector
  2. get_events                   — earnings, ex-div, news
  3. calculate_position_analysis  — Greeks + scenarios + decomposition
  4a. get_portfolio_greeks        — current portfolio snapshot (portfolio_positions)
  4b. calculate_hypothetical_impact — before/after/change (existing_positions + new)

All insights are informational. The agent describes what the numbers
show — it never prescribes a trade or expresses a preference between paths.
"""

import json

from anthropic import Anthropic
from core.runner import run_agent
from situational.tools import TOOLS, dispatch

SYSTEM_PROMPT = """You are a situational options analysis agent for Wealthsimple.

Your role is to produce clear, structured insights about an option position —
existing or potential — based on live market data and quantitative analysis.
All output is informational. You describe what the numbers show. You never
recommend a specific action or express a preference between paths.

═══ WORKFLOW ═══

CALL 1 — get_underlying_data(ticker)
  Retrieve live price, beta, dividend yield, sector, and risk-free rate.

CALL 2 — get_events(ticker)
  Retrieve upcoming earnings, ex-dividend dates, and recent news.
  Note how many days away each event is — this directly affects how
  time decay and IV behave for near-term positions.

CALL 3 — calculate_position_analysis(...)
  Use the live price from call 1 as the current underlying context.
  Use the contract's IV from the option chain (provided in the user message
  or fetched via get_option_chain if needed).
  Set days_forward to the days-to-earnings if earnings fall before expiry,
  so scenarios reflect the position at the relevant decision point.

CALL 4a — get_portfolio_greeks(positions) [only if portfolio_positions provided, no hypothetical]
  Aggregate beta-weighted delta and gamma across all existing option positions.
  Show current portfolio-level exposure as context for the position being analysed.

CALL 4b — calculate_hypothetical_impact(existing_positions, new_position)
  [only if existing_positions are provided AND a new position is being evaluated]
  Shows exactly how the new position shifts portfolio-level beta-weighted Greeks.
  existing_positions may be stocks/ETFs (position_type="equity", shares=N),
  options, or a mix — Greeks are only calculated for option positions.
  Equity positions contribute beta-weighted dollar delta only (gamma/theta/vega = 0).
  Use this instead of get_portfolio_greeks when the user wants to see
  the before/after/change breakdown for adding a new trade.

═══ INSIGHT STRUCTURE ═══

After the tool calls, produce a structured insight covering:

1. POSITION SUMMARY
   — ticker, type, strike, expiry, contracts, current underlying price
   — days to expiry; whether any event falls before expiry

2. GREEKS (for this investor's level)
   Beginner:     theta_per_day and vega_per_pct in plain language only
                 ("this position loses ~$X per day to time decay")
   Intermediate: delta, theta_per_day, vega_per_pct with brief mechanics
   Advanced:     all Greeks including gamma; beta-weighted delta if portfolio provided

3. SCENARIO ANALYSIS
   Present the scenario grid as a readable table (price move vs IV regime).
   Label the IV regimes meaningfully:
     iv_crush      → "IV drops 30% (e.g. post-earnings)"
     iv_unchanged  → "IV holds"
     iv_expansion  → "IV rises 30% (e.g. approaching event)"
   Highlight the most relevant scenario given the event calendar.

4. P&L DECOMPOSITION
   For the ±5% moves, show how much P&L came from each Greek.
   Use plain language to name each component:
     delta → "directional move"
     gamma → "acceleration/convexity"
     theta → "time decay over X days"
     vega  → "volatility change"
   Show total_approx vs total_exact and briefly note the residual
   (higher-order effects) if it is material (>10% of total_exact).

5. EVENT CONTEXT
   Connect the event calendar to the position's risk profile.
   If earnings fall before expiry, explain what IV crush typically means
   for this type of position — without predicting direction.

6. PORTFOLIO IMPACT [only if portfolio data provided]
   If hypothetical mode (existing_positions + new position):
     — Show before/after/change table for BW-delta, BW-gamma, theta, vega.
     — Describe in plain language what the shift means directionally and
       in terms of convexity (gamma) and decay (theta).
     — Note if the new position hedges, amplifies, or diversifies existing exposure.
   If current-portfolio mode (portfolio_positions only):
     — Show aggregate BW-delta and BW-gamma across all positions.
     — Describe overall portfolio directional bias.

═══ FRAMING RULES ═══
— Never use imperative language ("you should", "consider", "avoid").
— Describe situations and mechanics, not prescriptions.
— Calibrate technical depth to investor_level (provided in the user message).
— When presenting multiple paths (e.g. hold vs close before earnings),
  describe each path's mechanics neutrally — never rank or prefer one.
"""


def run_situational_agent(
    ticker: str,
    option_type: str,
    strike: float,
    expiry: str,
    contracts: int,
    entry_price: float,
    sigma: float,
    investor_level: str = "intermediate",
    days_forward: int = 0,
    portfolio_positions: list | None = None,
    existing_positions: list | None = None,
) -> dict:
    """
    Analyse a single option position and produce a structured situational insight.

    Args:
        ticker:              underlying ticker symbol
        option_type:         'call' or 'put'
        strike:              strike price
        expiry:              expiry date as 'YYYY-MM-DD'
        contracts:           number of contracts
        entry_price:         price per share when position was opened
                             (use current mid for new positions)
        sigma:               implied volatility as decimal (e.g. 0.65)
        investor_level:      'beginner' | 'intermediate' | 'advanced'
        days_forward:        project scenarios this many days forward (0 = today)
        portfolio_positions: existing option positions for portfolio-level Greek
                             aggregation (current snapshot, no hypothetical)
        existing_positions:  existing option positions for hypothetical impact
                             analysis — the position defined by ticker/strike/expiry
                             is treated as the new trade being evaluated

    Returns:
        The agent's final insight as a string (stored under 'insight' key),
        plus the structured tool outputs for audit / downstream use.
    """
    position_desc = (
        f"{contracts}× {ticker.upper()} {expiry} ${strike} {option_type.upper()} "
        f"(entry: ${entry_price:.2f}, IV: {sigma:.0%})"
    )

    portfolio_note = ""
    if existing_positions is not None:
        portfolio_note = (
            f"\n\nHypothetical mode: evaluate portfolio impact of adding this position.\n"
            f"Existing positions:\n"
            f"{json.dumps(existing_positions, indent=2)}"
        )
    elif portfolio_positions:
        portfolio_note = (
            f"\n\nPortfolio positions for Greeks aggregation:\n"
            f"{json.dumps(portfolio_positions, indent=2)}"
        )

    messages = [
        {
            "role": "user",
            "content": (
                f"Analyse this position: {position_desc}\n"
                f"Investor level: {investor_level}\n"
                f"Days forward for scenarios: {days_forward}"
                f"{portfolio_note}"
            ),
        }
    ]

    run_agent(SYSTEM_PROMPT, TOOLS, dispatch, messages, label=f"situational ({ticker.upper()})")

    # Extract the final text response from the assistant's last message
    insight = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg["content"]
            if isinstance(content, str):
                insight = content
            elif isinstance(content, list):
                texts = [b.text for b in content if hasattr(b, "text") and b.text]
                insight = "\n\n".join(texts)
            if insight:
                break

    return {"insight": insight, "messages": messages}


# ── Per-position analysis (Agent 1, zero tool calls) ─────────────────────────

_POSITION_SYSTEM_PROMPT = """You are a single-position options analyst for Wealthsimple.

All quantitative data has been pre-computed and is provided in the user message.
Do not make any tool calls.

Respond with exactly 3 bullet points. Each bullet must be one sentence, max 25 words.
No headers, no preamble, no closing line — only the 3 bullets.

• Bullet 1 — Directional/vol view: what the position expresses (net delta
  direction + whether it benefits or suffers from IV changes), calibrated
  to investor_level (no Greek names for beginner).

• Bullet 2 — Key scenario: the single most relevant outcome from the
  scenario data — reference the event calendar if an event falls before
  expiry, otherwise cite the ±10% or flat scenario that matters most.

• Bullet 3 — Main risk: the one factor most likely to cause a loss
  (e.g. IV crush post-earnings, theta burn on a short-dated OTM, delta
  whipsaw on a neutral position into a binary event).

Never use imperative language. Never prescribe an action.
"""


def run_position_analysis_agent(
    position: dict,
    events: dict,
    investor_level: str = "intermediate",
) -> str:
    """
    Brief narrative analysis of a single option position.
    Uses pre-computed data — zero tool calls, one API call.

    Args:
        position:       position dict containing 'analysis' sub-dict
                        (greeks, scenario_grid, pnl_decomposition, underlying)
                        plus ticker, option_type, strike, expiry, contracts.
        events:         events dict for this ticker from get_events.
        investor_level: 'beginner' | 'intermediate' | 'advanced'

    Returns:
        Insight string (150–250 words).
    """
    analysis = position.get("analysis", {})
    data = {
        "ticker":      position["ticker"],
        "option_type": position["option_type"],
        "strike":      position["strike"],
        "expiry":      position["expiry"],
        "contracts":   position["contracts"],
        "entry_price": position.get("entry_price", 0),
        "sigma":       position.get("sigma", 0),
        "underlying":  analysis.get("underlying", {}),
        "greeks":      analysis.get("greeks", {}),
        "key_scenarios": [
            row for row in analysis.get("scenario_grid", [])
            if row.get("price_move_pct") in (-10, 0, 10)
        ],
        "pnl_decomposition": analysis.get("pnl_decomposition", {}),
        "events": events,
    }

    user_message = (
        f"Investor level: {investor_level}\n\n"
        f"POSITION:\n{json.dumps(data, indent=2)}"
    )

    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=_POSITION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


# ── Stack analysis (Agent 2, zero tool calls) ─────────────────────────────────

_STACK_SYSTEM_PROMPT = """You are a portfolio options analysis agent for Wealthsimple.

All quantitative data — Greeks, scenario grids, P&L decompositions, underlying
prices, and upcoming events — has been pre-computed and is provided to you in
the user message. Per-position narrative summaries (Agent 1 outputs) are also
provided. Do not make any tool calls. Your role is synthesis only.

═══ WHAT TO IDENTIFY FIRST ═══

Before writing anything, identify what kind of position stack this is:

  Single leg        — one call or one put on one ticker
  Vertical spread   — two calls or two puts, same ticker, different strikes
  Straddle/strangle — call + put, same ticker
  Mixed tickers     — positions across different underlyings
  Other combination — describe what you see

This shapes the entire analysis. A spread has a defined max profit/loss that
neither leg alone reveals. Mixed-ticker positions interact through correlation.

═══ HOW TO USE AGENT 1 SUMMARIES ═══

Per-position summaries are provided under "POSITION SUMMARIES (Agent 1)".
Each summary covers: position identity, Greek exposures, key scenarios, event
risk, and main risk factor for that leg.

Use these as foundation — do not re-derive per-position mechanics.
Your value-add is the cross-position synthesis that the individual analyses
cannot produce: combined Greeks, position interactions, and portfolio-level
risk that only emerges when legs are viewed together.

═══ OUTPUT STRUCTURE ═══

1. POSITION SUMMARY
   One line per leg: ticker, type, strike, expiry, contracts, current delta,
   entry cost. If multiple legs form a recognisable structure, name it.

2. COMBINED GREEKS
   Interpret the portfolio-level numbers provided (BW-delta, BW-gamma,
   total theta, total vega). Explain what the NET exposure means:
   — Net direction: bullish / bearish / neutral / mixed
   — Theta: daily cost or daily income for the combined position
   — Vega: does the combined position benefit or suffer from IV expansion?
   — Gamma: is delta accelerating or stable?
   Beginner: plain language only (no Greek names, just dollar impact).
   Intermediate: Greek names with one-sentence mechanics each.
   Advanced: full Greek interpretation including cross-position interactions.

3. SCENARIO CONTEXT
   Refer to the scenario grids provided. For each leg, note:
   — The worst IV regime for that leg (usually iv_crush if long)
   — Approximate breakeven price move at iv_unchanged
   For multi-leg stacks, note where the combined position profits most.

4. EVENT RISK
   For each ticker with upcoming events, explain what the event means for
   the position type held — without predicting outcome or direction.
   If earnings fall before expiry: describe what IV expansion into earnings
   and IV crush after earnings typically does to this kind of position.
   If no events: state that clearly.

5. POSITION INTERACTIONS [skip if single ticker, single leg]
   Do the positions hedge each other, amplify exposure, or diversify across
   uncorrelated underlyings? Name the dominant risk driver across the stack.

═══ FRAMING RULES ═══
— Never use imperative language ("you should", "consider", "avoid").
— Describe situations and mechanics, not prescriptions.
— Calibrate depth to investor_level in the user message.
— Never rank or prefer one path over another.
"""


def run_stack_analysis_agent(
    positions: list[dict],
    portfolio_summary: dict,
    events_by_ticker: dict,
    investor_level: str = "intermediate",
    position_insights: list[str] | None = None,
) -> str:
    """
    Synthesise a multi-position stack into one structured insight.

    All data is pre-computed — no tool calls are made. This is a single
    API call regardless of how many positions are in the stack.

    Args:
        positions:         list of position dicts, each containing the
                           fields from run_situational_agent plus an
                           'analysis' sub-dict (greeks, scenario_grid,
                           pnl_decomposition, underlying).
        portfolio_summary: beta-weighted Greek totals from get_portfolio_greeks.
        events_by_ticker:  {ticker: events_dict} pre-fetched from get_events.
        investor_level:    'beginner' | 'intermediate' | 'advanced'
        position_insights: optional list of Agent 1 per-position narratives,
                           parallel to positions. Passed as context for synthesis.

    Returns:
        The insight string.
    """
    # Build a compact, structured representation of each position
    positions_data = []
    for pos in positions:
        analysis = pos.get("analysis", {})
        entry = {
            "ticker":      pos["ticker"],
            "option_type": pos["option_type"],
            "strike":      pos["strike"],
            "expiry":      pos["expiry"],
            "contracts":   pos["contracts"],
            "entry_price": pos.get("entry_price", 0),
            "sigma":       pos.get("sigma", 0),
            "underlying":  analysis.get("underlying", {}),
            "greeks":      analysis.get("greeks", {}),
            # Condensed scenario: just the ±10% and flat rows
            "key_scenarios": [
                row for row in analysis.get("scenario_grid", [])
                if row.get("price_move_pct") in (-10, 0, 10)
            ],
            "pnl_decomposition": analysis.get("pnl_decomposition", {}),
        }
        positions_data.append(entry)

    # Build per-position summary block if Agent 1 outputs are provided
    summaries_block = ""
    if position_insights:
        lines = []
        for i, (pos, insight) in enumerate(zip(positions, position_insights), 1):
            header = (
                f"Position {i}: {pos['contracts']}× {pos['ticker']} "
                f"{pos['expiry']} ${pos['strike']} {pos['option_type'].upper()}"
            )
            lines.append(f"--- {header} ---\n{insight}")
        summaries_block = (
            "\n\nPOSITION SUMMARIES (Agent 1):\n"
            + "\n\n".join(lines)
        )

    user_message = (
        f"Investor level: {investor_level}\n\n"
        f"POSITION STACK ({len(positions)} position{'s' if len(positions) != 1 else ''}):\n"
        f"{json.dumps(positions_data, indent=2)}\n\n"
        f"PORTFOLIO GREEKS:\n"
        f"{json.dumps(portfolio_summary, indent=2)}\n\n"
        f"EVENTS BY TICKER:\n"
        f"{json.dumps(events_by_ticker, indent=2)}"
        f"{summaries_block}"
    )

    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=_STACK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
