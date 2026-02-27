"""
Tool implementations and schemas for the portfolio construction agent.

Three tools, one agent pass:
  1. get_portfolio               — returns all positions + contract availability
  2. screen_options_opportunities — filters level-appropriate strategies per position
  3. store_portfolio_plan         — persists the construction plan JSON
"""

import json
from datetime import datetime
from pathlib import Path

from core.gates import STRATEGY_GATES, LEVEL_ORDER
from portfolio.positions import PORTFOLIO


# ─── Implementations ──────────────────────────────────────────────────────────

def get_portfolio() -> dict:
    """Returns the full portfolio with ETFs, stocks, and contract availability."""
    all_positions = PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
    return {
        "total_value": PORTFOLIO["total_value"],
        "etf_value": sum(p["market_value"] for p in PORTFOLIO["etfs"]),
        "stock_value": sum(p["market_value"] for p in PORTFOLIO["stocks"]),
        "positions": all_positions,
        "covered_call_eligible": [
            p for p in all_positions if p["contracts_available"] > 0
        ],
        "note": (
            "Covered calls require 100 shares per contract. Only positions with "
            "contracts_available > 0 can write covered calls. All positions support "
            "protective puts (buying puts) regardless of share count."
        ),
    }


def screen_options_opportunities(investor_level: str) -> dict:
    """
    For each position, determine which options strategies are appropriate given:
      1. The investor's knowledge level (gates from STRATEGY_GATES)
      2. The position's role (anchor positions should not have covered calls;
         hold_growth positions are protected with puts, not capped with calls)
      3. The 100-share contract constraint (covered calls require contracts_available >= 1)

    Role-based strategy logic:
      anchor       (SPY, QQQ):    protective puts ONLY — no CC; capping these conflicts
                                   with the buy-and-hold thesis
      income_etf   (SCHD):        covered calls are the primary strategy
      smallcap_value (IWM):       protective puts; approaching CC via CSPs if desired
      hold_growth  (NVDA, TSLA…): protective puts + opportunistic long calls; NO covered
                                   calls — capping upside defeats the purpose of holding
      income_value (MSFT, PLTR…): covered calls where eligible; CSPs to accumulate
                                   toward 100-share threshold on PLTR and WFC
    """
    investor_rank = LEVEL_ORDER.get(investor_level, 0)
    all_positions = PORTFOLIO["etfs"] + PORTFOLIO["stocks"]

    def level_allowed(strategy: str) -> bool:
        gate = STRATEGY_GATES.get(strategy, {})
        required = gate.get("min_level", "advanced")
        return investor_rank >= LEVEL_ORDER[required]

    # Roles where covered calls should NOT be recommended regardless of eligibility
    NO_COVERED_CALL_ROLES = {"anchor", "hold_growth"}

    opportunities: dict = {
        "investor_level": investor_level,
        "covered_calls": [],
        "protective_puts": [],
        "cash_secured_puts": [],
        "buy_calls_puts": [],
        "blocked_strategies": [],
        "constraint_notes": [],
    }

    # ── Covered calls ──────────────────────────────────────────────────────────
    if level_allowed("covered_calls"):
        cc_eligible = [
            p for p in all_positions
            if p["contracts_available"] > 0 and p.get("role") not in NO_COVERED_CALL_ROLES
        ]
        role_blocked = [
            p for p in all_positions
            if p["contracts_available"] > 0 and p.get("role") in NO_COVERED_CALL_ROLES
        ]

        for p in cc_eligible:
            est_monthly = round(p["price"] * 0.015 * p["contracts_available"] * 100, 0)
            opportunities["covered_calls"].append({
                "ticker": p["ticker"],
                "role": p.get("role"),
                "shares": p["shares"],
                "contracts": p["contracts_available"],
                "suggested_delta": "0.15–0.20 (OTM, low assignment risk)",
                "suggested_expiry": "30–45 days",
                "est_monthly_premium_usd": est_monthly,
                "annual_income_est_usd": round(est_monthly * 12, 0),
            })

        # Explain why share-eligible but role-inappropriate positions are excluded
        if role_blocked:
            opportunities["constraint_notes"].append({
                "strategy": "covered_calls",
                "type": "role_conflict",
                "note": (
                    f"{[p['ticker'] for p in role_blocked]} have enough shares for covered "
                    f"calls but are held as buy-and-hold anchor positions. Writing covered "
                    f"calls would cap their upside, conflicting with the holding thesis. "
                    f"Use protective puts instead."
                ),
            })

        # Explain positions that fall short on share count
        share_ineligible = [
            p for p in all_positions
            if p["contracts_available"] == 0 and p.get("role") not in NO_COVERED_CALL_ROLES
        ]
        if share_ineligible:
            # Separate into "approaching" (≥50 shares) vs "far off" (<50 shares)
            approaching = [p for p in share_ineligible if p["shares"] >= 50]
            far_off = [p for p in share_ineligible if p["shares"] < 50]
            opportunities["constraint_notes"].append({
                "strategy": "covered_calls",
                "type": "insufficient_shares",
                "approaching_threshold": [
                    {"ticker": p["ticker"], "shares": p["shares"], "needed": 100 - p["shares"]}
                    for p in approaching
                ],
                "far_from_threshold": [
                    {"ticker": p["ticker"], "shares": p["shares"], "needed": 100 - p["shares"]}
                    for p in far_off
                ],
                "note": (
                    "Use cash-secured puts on 'approaching' positions to collect premium "
                    "while accumulating shares toward the 100-share covered call threshold."
                ),
            })
    else:
        opportunities["blocked_strategies"].append({
            "strategy": "covered_calls",
            "required_level": "intermediate",
            "current_level": investor_level,
        })

    # ── Protective puts ────────────────────────────────────────────────────────
    if level_allowed("protective_puts"):
        # Prioritise: anchor + hold_growth (these are the positions most worth protecting)
        priority_roles = {"anchor", "hold_growth", "income_etf"}
        for p in sorted(all_positions, key=lambda x: x.get("role", "") not in priority_roles):
            est_monthly_cost = round(p["market_value"] * 0.007, 0)
            opportunities["protective_puts"].append({
                "ticker": p["ticker"],
                "role": p.get("role"),
                "position_value": p["market_value"],
                "priority": "high" if p.get("role") in priority_roles else "standard",
                "suggested_strike": f"~10% OTM (${round(p['price'] * 0.90, 0)})",
                "suggested_expiry": "60–90 days",
                "est_monthly_cost_usd": est_monthly_cost,
                "rationale": (
                    "Anchor — protecting core buy-and-hold position."
                    if p.get("role") == "anchor" else
                    "Growth position — high upside but needs downside floor."
                    if p.get("role") == "hold_growth" else
                    "Standard portfolio protection."
                ),
            })
    else:
        opportunities["blocked_strategies"].append({
            "strategy": "protective_puts",
            "required_level": "intermediate",
            "current_level": investor_level,
        })

    # ── Cash-secured puts ──────────────────────────────────────────────────────
    if level_allowed("cash_secured_puts"):
        # Best targets: income_value positions approaching 100 shares (PLTR, WFC)
        accumulation_targets = [
            p for p in all_positions
            if p["contracts_available"] == 0
            and p.get("role") == "income_value"
            and p["shares"] >= 30      # worth pursuing; too few shares = too far off
        ]
        for p in accumulation_targets:
            shares_needed = 100 - p["shares"]
            cash_required = round(p["price"] * 0.95 * 100, 0)
            est_premium = round(p["price"] * 0.008 * 100, 0)
            opportunities["cash_secured_puts"].append({
                "ticker": p["ticker"],
                "current_shares": p["shares"],
                "shares_to_cc_threshold": shares_needed,
                "suggested_strike": f"~5% OTM (${round(p['price'] * 0.95, 0)})",
                "cash_required_usd": cash_required,
                "est_premium_usd": est_premium,
                "purpose": (
                    f"Collect ${est_premium} premium per contract while working toward "
                    f"{shares_needed} more shares needed for covered call eligibility."
                ),
            })
    else:
        opportunities["blocked_strategies"].append({
            "strategy": "cash_secured_puts",
            "required_level": "intermediate",
            "current_level": investor_level,
        })

    # ── Buy calls/puts (available to everyone) ────────────────────────────────
    if level_allowed("buy_calls_puts"):
        opportunities["buy_calls_puts"] = {
            "available_on": [p["ticker"] for p in all_positions],
            "best_for_speculation": [
                p["ticker"] for p in all_positions if p.get("role") == "hold_growth"
            ],
            "note": (
                "Buying calls or puts is available on all positions. "
                "Most useful for speculative plays on hold_growth positions "
                "(NVDA, TSLA, META, SHOP, AMZN) where the risk is limited to the premium."
            ),
        }

    return opportunities


def store_portfolio_plan(plan: dict, filepath: str = "profiles/portfolio_plan.json") -> dict:
    """Persists the portfolio construction plan as a JSON file."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    plan["generated_at"] = datetime.now().isoformat()
    with open(filepath, "w") as f:
        json.dump(plan, f, indent=2)
    return {"success": True, "filepath": filepath}


# ─── Dispatch ─────────────────────────────────────────────────────────────────

def dispatch(name: str, tool_input: dict) -> str:
    if name == "get_portfolio":
        result = get_portfolio()
    elif name == "screen_options_opportunities":
        result = screen_options_opportunities(tool_input["investor_level"])
    elif name == "store_portfolio_plan":
        result = store_portfolio_plan(
            tool_input["plan"],
            tool_input.get("filepath", "profiles/portfolio_plan.json"),
        )
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


# ─── Schemas ──────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_portfolio",
        "description": (
            "Retrieve the investor's full $200k portfolio: 4 ETFs ($30k each) and "
            "10 individual stocks split across hold_growth ($6k each) and income_value "
            "($10k each) tiers. Includes share counts, current prices, market values, "
            "and how many covered call contracts each position supports."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "screen_options_opportunities",
        "description": (
            "For each portfolio position, identify which options strategies are "
            "available at the investor's level. Returns covered call candidates "
            "(positions with 100+ shares), protective put candidates (all positions), "
            "cash-secured put targets (positions to accumulate toward 100 shares), "
            "and blocked strategies with the reason. Also returns constraint notes "
            "explaining why certain positions can't use certain strategies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investor_level": {
                    "type": "string",
                    "enum": ["beginner", "intermediate", "advanced"],
                    "description": "The investor's assessed knowledge level.",
                }
            },
            "required": ["investor_level"],
        },
    },
    {
        "name": "store_portfolio_plan",
        "description": "Persist the completed portfolio construction plan as a JSON file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "object",
                    "description": (
                        "Complete plan with: investor_level, portfolio_summary, "
                        "strategy_recommendations (income, protection, accumulation), "
                        "position_sizing_rules, estimated_annual_impact, "
                        "portfolio_greeks_overview, priority_actions."
                    ),
                },
                "filepath": {
                    "type": "string",
                    "description": "Output path (default: profiles/portfolio_plan.json).",
                },
            },
            "required": ["plan"],
        },
    },
]
