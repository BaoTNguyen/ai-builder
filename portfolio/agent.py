"""
Portfolio construction agent: system prompt + agentic loop.

Exactly 3 tool-call turns:
  1. get_portfolio               → see all positions + contract availability
  2. screen_options_opportunities → level-filtered strategy candidates per position
  3. store_portfolio_plan         → persist the final construction plan

Claude's reasoning work — selecting strategies, sizing positions, estimating
income/protection, writing priority actions — happens between turns 2 and 3.
"""

import json

from core.runner import run_agent
from portfolio.tools import TOOLS, dispatch

SYSTEM_PROMPT = """You are a portfolio construction agent for Wealthsimple.

Given an investor's $200k portfolio and their assessed knowledge level, you produce
a concrete options overlay plan that enhances the portfolio without overcomplicating it.

═══ WORKFLOW — exactly 3 tool calls ═══

CALL 1 — get_portfolio()
  Understand every position: ticker, shares, price, market value, contracts_available.
  Note which positions can write covered calls vs which fall short of 100 shares.

CALL 2 — screen_options_opportunities(investor_level)
  Get level-filtered opportunities: covered calls, protective puts, cash-secured puts.
  Read the constraint_notes — they explain real limitations (e.g., 9 of 10 stocks
  can't write covered calls because $2k positions don't reach 100 shares).

REASONING — before call 3, build the plan:

  Think through these dimensions:

  INCOME STRATEGIES (covered calls, if level allows):
    - Which positions have contracts_available > 0?
    - For each: recommend strike delta, expiry window, estimated monthly premium
    - Calculate total estimated annual income from the overlay

  PROTECTION STRATEGIES (protective puts, if level allows):
    - Which positions have the most concentration risk?
    - Suggest a tiered approach: full protection on top holdings vs spot hedges
    - Estimate monthly cost and net income after hedging

  ACCUMULATION STRATEGIES (cash-secured puts, if level allows):
    - Which stocks are one step away from covered call eligibility?
    - Which stocks does the investor most want to own more of?
    - Suggest CSPs that collect premium while working toward 100-share thresholds

  POSITION SIZING RULES by level:
    Beginner:     max 2% of portfolio per options trade, 5% total options exposure
    Intermediate: max 5% per trade, 15% total exposure
    Advanced:     max 10% per trade, 25% total exposure

  PORTFOLIO GREEKS OVERVIEW:
    Describe the aggregate delta, theta, and vega exposure the plan creates.
    Keep it in plain English — e.g., "Portfolio gains ~$X for every $1 SPY moves up."

  PRIORITY ACTIONS:
    List 3–5 specific first steps ordered by impact and simplicity.
    Each action: ticker, strategy, specific strike/expiry suggestion, rationale.

CALL 3 — store_portfolio_plan(plan)
  Build and store with this shape:
  {
    "investor_level": str,
    "portfolio_summary": {
      "total_value": float,
      "etf_value": float,
      "stock_value": float,
      "covered_call_eligible_positions": [str],   // tickers only
      "covered_call_ineligible_count": int,
      "ineligible_reason": str
    },
    "strategy_recommendations": {
      "income": [
        {
          "ticker": str,
          "strategy": "covered_call",
          "contracts": int,
          "suggested_strike_delta": str,
          "suggested_expiry": str,
          "est_monthly_premium_usd": float,
          "rationale": str
        }
      ],
      "protection": [
        {
          "ticker": str,
          "strategy": "protective_put",
          "suggested_strike": str,
          "suggested_expiry": str,
          "est_monthly_cost_usd": float,
          "rationale": str
        }
      ],
      "accumulation": [
        {
          "ticker": str,
          "strategy": "cash_secured_put",
          "current_shares": int,
          "shares_to_goal": int,
          "suggested_strike": str,
          "est_premium_usd": float,
          "rationale": str
        }
      ]
    },
    "position_sizing_rules": {
      "max_per_trade_pct": float,
      "max_total_exposure_pct": float,
      "max_per_trade_usd": float,
      "max_total_exposure_usd": float
    },
    "estimated_annual_impact": {
      "gross_income_usd": float,
      "protection_cost_usd": float,
      "net_impact_usd": float,
      "net_impact_pct_of_portfolio": float
    },
    "portfolio_greeks_overview": {
      "delta_summary": str,
      "theta_summary": str,
      "vega_summary": str
    },
    "priority_actions": [
      {
        "rank": int,
        "ticker": str,
        "action": str,
        "specific_trade": str,
        "rationale": str
      }
    ]
  }
"""


def run_portfolio_agent(
    investor_level: str,
    filepath: str = "profiles/portfolio_plan.json",
) -> dict:
    """
    Build an options overlay plan for the $100k portfolio.

    Args:
        investor_level: "beginner", "intermediate", or "advanced"
        filepath:       where to write the plan JSON

    Returns:
        The portfolio construction plan dict (also written to filepath).
    """
    messages = [
        {
            "role": "user",
            "content": (
                f"Investor level: {investor_level}\n"
                f"Build the options overlay plan and save it to: {filepath}"
            ),
        }
    ]

    run_agent(SYSTEM_PROMPT, TOOLS, dispatch, messages, label=f"portfolio ({investor_level})")

    try:
        with open(filepath) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "Plan was not stored by the agent."}
