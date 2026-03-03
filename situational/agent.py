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
Each bullet MUST start on its own line. Use this exact format:

• [sentence]

• [sentence]

• [sentence]

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


# ── Per-strategy analysis (same-expiry group, zero tool calls) ───────────────

_STRATEGY_SYSTEM_PROMPT = """You are a multi-leg options strategy analyst for Wealthsimple.

All quantitative data — net Greeks, payoff statistics, per-leg Agent 1 summaries,
and events — has been pre-computed and is provided in the user message.
Do not make any tool calls.

Respond with exactly 3 bullet points. Each bullet must be one sentence, max 30 words.
No headers, no preamble, no closing line — only the 3 bullets.
Each bullet MUST start on its own line. Use this exact format:

• [sentence]

• [sentence]

• [sentence]

• Bullet 1 — Structure & payoff: state the net cost/credit, max profit, max loss,
  and breakeven price in plain terms. Calibrate to investor_level (no Greek names
  for beginner).

• Bullet 2 — Net exposure: explain what the combined Greeks produce that the
  individual legs alone do not — focus on how the legs offset or amplify each
  other's delta, theta, and vega.

• Bullet 3 — Critical condition: the single condition (price level, IV event,
  or time dynamic) that determines whether this structure succeeds or fails.

Never use imperative language. Never prescribe an action.
"""


def run_strategy_analysis_agent(
    strategy_label: str,
    group: list[dict],
    net_greeks: dict,
    payoff_stats: dict,
    events: dict,
    investor_level: str = "intermediate",
    position_insights: list[str] | None = None,
    equity_shares: int = 0,
    equity_entry: float = 0.0,
) -> str:
    """
    Concise analysis of a same-ticker, same-expiry multi-leg strategy.
    Zero tool calls, one API call, max 384 tokens.

    Args:
        strategy_label:    e.g. "Bull Call Spread", "Covered Call"
        group:             list of position dicts for this strategy group
        net_greeks:        summed Greeks dict across all legs
        payoff_stats:      {max_profit, max_loss, breakevens: [float], net_cost}
        events:            events dict for the ticker
        investor_level:    'beginner' | 'intermediate' | 'advanced'
        position_insights: Agent 1 per-leg narratives (optional)
        equity_shares:     portfolio equity shares involved (0 if none)
        equity_entry:      equity book entry price
    """
    legs = []
    for pos in group:
        direction = "LONG" if pos["contracts"] > 0 else "SHORT"
        legs.append({
            "direction":   direction,
            "option_type": pos["option_type"],
            "strike":      pos["strike"],
            "contracts":   abs(pos["contracts"]),
            "entry_price": pos.get("entry_price", 0),
        })

    data: dict = {
        "strategy":    strategy_label,
        "ticker":      group[0]["ticker"],
        "expiry":      group[0]["expiry"],
        "legs":        legs,
        "net_greeks":  net_greeks,
        "payoff_stats": payoff_stats,
        "events":      events,
    }
    if equity_shares:
        data["equity"] = {"shares": equity_shares, "entry_price": equity_entry}

    summaries_block = ""
    if position_insights:
        lines = []
        for i, (pos, ins) in enumerate(zip(group, position_insights), 1):
            direction = "LONG" if pos["contracts"] > 0 else "SHORT"
            lines.append(
                f"Leg {i} ({direction} {pos['option_type'].upper()} "
                f"${pos['strike']}): {ins}"
            )
        summaries_block = "\n\nPER-LEG SUMMARIES (Agent 1):\n" + "\n\n".join(lines)

    user_message = (
        f"Investor level: {investor_level}\n\n"
        f"STRATEGY DATA:\n{json.dumps(data, indent=2)}"
        f"{summaries_block}"
    )

    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=384,
        system=_STRATEGY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


# ── Stack analysis (Agent 2, zero tool calls) ─────────────────────────────────

_STACK_SYSTEM_PROMPT = """You are a portfolio options analysis agent for Wealthsimple.

All quantitative data — Greeks, scenario grids, P&L decompositions, underlying
prices, and upcoming events — has been pre-computed and is provided to you in
the user message. Per-position narrative summaries (Agent 1 outputs) are also
provided. Do not make any tool calls. Your role is synthesis only.

═══ OUTPUT STRUCTURE (3 sections, no more) ═══

1. POSITIONS & SCENARIOS
   One line per leg: ticker · type · strike · expiry · contracts · entry cost.
   Name the recognisable structure if one exists (e.g. "Bull Call Spread",
   "Covered Call", "Straddle"). Then in 2–3 sentences cover the combined
   scenario picture: the breakeven price move at iv_unchanged, the worst IV
   regime, and the best-case scenario. Be concrete — use dollar P&L numbers
   from the scenario grids.

2. GREEKS & EVENT RISK
   In 3–4 sentences: state the net directional bias (bullish/bearish/neutral),
   the combined theta ($/day income or cost), and net vega (benefits or suffers
   from IV expansion). For intermediate/advanced, name the Greeks; for beginner,
   use plain dollar language only. Then in 1–2 sentences connect the event
   calendar directly to those Greek exposures — e.g. how IV expansion into
   earnings affects vega P&L, or how IV crush after earnings affects theta
   vs vega. If no events: one sentence stating that.

3. POSITION INTERACTIONS [omit entirely if single ticker, single leg]
   In 2–3 sentences: describe how the legs interact — hedging, amplifying, or
   diversifying — and name the single dominant risk driver across the stack.
   For mixed-ticker positions, note correlation effects. Keep it tight.

═══ FORMATTING RULES ═══
— Use **bold** for section labels (e.g. **1. Positions & Scenarios**), never
  markdown headings (##, ###) — these render at incorrect sizes in the UI.
— Never use imperative language ("you should", "consider", "avoid").
— Describe situations and mechanics, not prescriptions.
— Calibrate depth to investor_level in the user message.
— Never rank or prefer one path over another.
— No filler phrases ("It's worth noting", "Keep in mind"). Every sentence
  must carry quantitative or structural content.
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
        max_tokens=1024,
        system=_STACK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


# ── Portfolio impact agent (full equity + options, zero tool calls) ───────────

_PORTFOLIO_IMPACT_SYSTEM_PROMPT = """You are a portfolio-level options impact analyst for Wealthsimple.

All quantitative data has been pre-computed and is provided in the user message.
Do not make any tool calls.

You receive:
  — The investor's full equity portfolio (tickers, market values, weights)
  — All options positions being added, with their per-position and per-strategy insights
  — Beta-weighted Greeks for the options overlay alone
  — Beta-weighted Greeks for the combined equity + options portfolio
  — Upcoming events across all tickers with options

Respond with exactly 4 bullet points. Each bullet must be one sentence, max 35 words.
No headers, no preamble, no closing line — only the 4 bullets.
Each bullet MUST start on its own line. Use this exact format:

• [sentence]

• [sentence]

• [sentence]

• [sentence]

• Bullet 1 — Directional shift: compare the options overlay's net beta-weighted
  delta against the equity-only baseline — does the overlay add, reduce, or
  neutralise directional exposure? Calibrate to investor_level (no Greek names
  for beginner).

• Bullet 2 — Income / cost profile: state total theta in $/day and as an
  annualised % of portfolio value, and total vega as $ per 1% IV move.
  Distinguish whether the overlay is net income-generating or net cost-bearing.

• Bullet 3 — Concentration & event risk: name which option position(s) carry
  the most meaningful risk relative to their underlying weight in the portfolio,
  especially where events fall before expiry.

• Bullet 4 — Portfolio characterisation: in one sentence, describe what this
  combined equity + options book expresses — hedged growth, income overlay,
  speculative upside, or another coherent profile.

Never use imperative language. Never prescribe an action.
"""


def run_portfolio_impact_agent(
    equity_holdings: list[dict],
    options_positions: list[dict],
    options_greeks: dict,
    full_portfolio_greeks: dict,
    total_portfolio_value: float,
    events_by_ticker: dict,
    investor_level: str = "intermediate",
    strategy_insights: dict | None = None,
) -> str:
    """
    Portfolio-level analysis of the combined equity + options book.

    Situates the entire options overlay within the full equity portfolio,
    producing four bullets on delta shift, income/cost profile, concentration
    risk, and overall portfolio characterisation.

    Zero tool calls, one API call, max 512 tokens.

    Args:
        equity_holdings:       list of {ticker, shares, book_price,
                                market_value, weight_pct, role}
        options_positions:     list of position dicts (with 'insight' if available)
        options_greeks:        beta-weighted Greeks for options only
        full_portfolio_greeks: beta-weighted Greeks for equity + options combined
        total_portfolio_value: current marked-to-market portfolio value ($)
        events_by_ticker:      {ticker: events_dict}
        investor_level:        'beginner' | 'intermediate' | 'advanced'
        strategy_insights:     optional {strategy_key: insight_text} from
                               per-strategy analysis buttons
    """
    # Compact options summary — strip raw scenario grids to save tokens
    options_summary = []
    for pos in options_positions:
        direction = "LONG" if pos["contracts"] > 0 else "SHORT"
        entry = {
            "direction":   direction,
            "ticker":      pos["ticker"],
            "option_type": pos["option_type"],
            "strike":      pos["strike"],
            "expiry":      pos["expiry"],
            "contracts":   abs(pos["contracts"]),
            "insight":     pos.get("insight", ""),
        }
        analysis = pos.get("analysis", {})
        if analysis.get("greeks"):
            entry["greeks"] = analysis["greeks"]
        options_summary.append(entry)

    # Theta annualised as % of portfolio
    theta_per_day   = options_greeks.get("total_theta", 0)
    theta_annual_pct = (
        abs(theta_per_day) * 365 / total_portfolio_value * 100
        if total_portfolio_value else 0
    )

    data: dict = {
        "equity_portfolio": {
            "total_value_usd": round(total_portfolio_value, 0),
            "holdings":        equity_holdings,
        },
        "options_overlay": {
            "positions":       options_summary,
            "greeks":          options_greeks,
            "theta_annual_pct_of_portfolio": round(theta_annual_pct, 3),
        },
        "full_portfolio_greeks": full_portfolio_greeks,
        "events_by_ticker":      events_by_ticker,
    }

    if strategy_insights:
        data["strategy_insights"] = strategy_insights

    user_message = (
        f"Investor level: {investor_level}\n\n"
        f"PORTFOLIO IMPACT DATA:\n{json.dumps(data, indent=2)}"
    )

    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=_PORTFOLIO_IMPACT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
