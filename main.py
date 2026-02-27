"""
Demo entry point.

Runs both agents sequentially against sample data and demonstrates the
paper trading gate flow. The investor profile from Agent 1 feeds into
Agent 2, mirroring how the real app wires them together.
"""

import json
from dotenv import load_dotenv

load_dotenv()

from assessment.agent import run_assessment_agent
from assessment.tools import check_action_permission, create_paper_portfolio
from portfolio.agent import run_portfolio_agent


def separator(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# ── Sample survey answers ─────────────────────────────────────────────────────
# Simulates an intermediate investor: solid fundamentals, gaps in assignment
# mechanics, expiration management, gamma, and volatility crush.
SAMPLE_ANSWERS = {
    1: "B",  # ✓ premium paid
    2: "B",  # ✓ covered calls
    3: "B",  # ✓ time decay / theta
    4: "A",  # ✗ buys more stock   (missed: protective puts)
    5: "B",  # ✓ delta
    6: "A",  # ✗ keeps stock       (missed: assignment mechanics)
    7: "C",  # ✓ cash-secured puts
    8: "B",  # ✓ IV expands pre-earnings
    9: "A",  # ✗ lets all expire   (missed: active expiry management)
    10: "C", # ✗ gamma = safety    (missed: gamma risk)
    11: "B", # ✓ iron condor stays in range
    12: "A", # ✗ time decay        (missed: IV crush)
}

if __name__ == "__main__":

    # ── Agent 1: Assessment ───────────────────────────────────────────────────
    separator("AGENT 1 — OPTIONS KNOWLEDGE ASSESSMENT")
    profile = run_assessment_agent(SAMPLE_ANSWERS)

    print(f"Level:          {profile['level']}")
    print(f"Score:          {profile['raw_score']}  ({profile['weighted_score_pct']}% weighted)")
    print(f"Strengths:      {len(profile['strengths'])} concepts")
    print(f"Weaknesses:     {len(profile['weaknesses'])} concepts")
    print(f"Available actions:")
    for action in profile["available_actions"]:
        print(f"  • {action}")

    # ── Gate: beginner tries a blocked action ─────────────────────────────────
    separator("ACTION GATE — Beginner attempts 'iron_condor'")

    gate_result = check_action_permission("iron_condor", "beginner")
    print(f"Allowed: {gate_result['allowed']}")
    print(f"Required level: {gate_result['required_level']}")
    print("\nAlternatives offered:")
    for alt in gate_result["alternatives"]:
        print(f"  [{alt['action']}]  {alt['label']}")
        print(f"    {alt['description']}")

    # Simulate user choosing paper trading
    separator("USER CHOSE PAPER TRADING")
    paper_result = create_paper_portfolio(
        investor_profile=profile,
        attempted_action="iron_condor",
    )
    paper = paper_result["paper_portfolio"]
    print(f"Paper portfolio created: {paper_result['filepath']}")
    print(f"Total value:    ${paper['total_value']:,.0f}")
    print(f"Positions:      {len(paper['positions'])}")
    print(f"Practicing:     {paper['attempted_action'].replace('_', ' ')}")
    print("\nFirst 3 positions in paper portfolio:")
    for pos in paper["positions"][:3]:
        print(f"  {pos['ticker']:6s}  {pos['shares']:>4} shares @ ${pos['current_price']:,.2f}  (paper trades: {pos['paper_trades']})")

    # ── Agent 2: Portfolio Construction ───────────────────────────────────────
    separator("AGENT 2 — PORTFOLIO CONSTRUCTION")
    plan = run_portfolio_agent(investor_level=profile["level"])

    print(f"Level:          {plan['investor_level']}")
    print(f"Portfolio:      ${plan['portfolio_summary']['total_value']:,.0f}")
    print(f"CC eligible:    {plan['portfolio_summary']['covered_call_eligible_positions']}")

    print("\nIncome strategies:")
    for s in plan["strategy_recommendations"].get("income", []):
        print(f"  {s['ticker']:6s} — {s['contracts']}x covered call  ~${s['est_monthly_premium_usd']:,.0f}/mo")

    print("\nAccumulation strategies (building toward covered call eligibility):")
    for s in plan["strategy_recommendations"].get("accumulation", [])[:3]:
        print(f"  {s['ticker']:6s} — sell CSP @ {s['suggested_strike']}  ~${s['est_premium_usd']:,.0f} premium")

    print("\nEstimated annual impact:")
    impact = plan["estimated_annual_impact"]
    print(f"  Gross income:     ${impact['gross_income_usd']:,.0f}")
    print(f"  Protection cost:  ${impact['protection_cost_usd']:,.0f}")
    print(f"  Net impact:       ${impact['net_impact_usd']:,.0f}  ({impact['net_impact_pct_of_portfolio']:.1f}% of portfolio)")

    print("\nPriority actions:")
    for action in plan.get("priority_actions", []):
        print(f"  {action['rank']}. {action['ticker']} — {action['action']}")
        print(f"     {action['specific_trade']}")
