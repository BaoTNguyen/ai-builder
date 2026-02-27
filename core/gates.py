"""
Platform-wide action gate definitions.

These are not portfolio-specific — they describe which options strategies
require which investor level and under what trading context. Both the
assessment agent (gating feature access) and the portfolio agent (filtering
opportunities) import from here.

Two layers:
  STRATEGY_GATES  — named strategy → minimum level
  CONTEXT_GATES   — context signals (leg count, concurrency, etc.) → minimum level
  LEVEL_ORDER     — maps level names to sortable integers
"""

# Named strategy gates: what a user explicitly asks to do.
STRATEGY_GATES: dict[str, dict] = {
    # Beginner
    "buy_calls_puts": {
        "min_level": "beginner",
        "legs": 1,
        "risk": "defined",
        "description": "Single-leg directional bet; max loss is the premium paid.",
    },
    "paper_trading": {
        "min_level": "beginner",
        "description": "Risk-free practice on a portfolio replica.",
    },
    # Intermediate
    "covered_calls": {
        "min_level": "intermediate",
        "legs": 1,
        "risk": "defined_with_assignment",
        "description": "Sell call against 100 owned shares; caps upside, generates income.",
    },
    "protective_puts": {
        "min_level": "intermediate",
        "legs": 1,
        "risk": "defined",
        "description": "Buy put to insure a long stock/ETF position.",
    },
    "cash_secured_puts": {
        "min_level": "intermediate",
        "legs": 1,
        "risk": "defined_with_assignment",
        "description": "Sell put with full cash collateral; premium income + potential stock entry.",
    },
    "vertical_spread": {
        "min_level": "intermediate",
        "legs": 2,
        "risk": "defined",
        "description": "Buy and sell calls (or puts) at different strikes, same expiry.",
    },
    "collar": {
        "min_level": "intermediate",
        "legs": 2,
        "risk": "defined",
        "description": "Protective put + covered call on the same position.",
    },
    # Advanced
    "iron_condor": {
        "min_level": "advanced",
        "legs": 4,
        "risk": "defined",
        "description": "Sell OTM call spread + sell OTM put spread; profits from low volatility.",
    },
    "butterfly": {
        "min_level": "advanced",
        "legs": 3,
        "risk": "defined",
        "description": "Three-strike strategy that profits if stock pins near the middle strike.",
    },
    "calendar_spread": {
        "min_level": "advanced",
        "legs": 2,
        "multi_expiry": True,
        "risk": "defined",
        "description": "Same strike, different expiries; profits from time decay differential.",
    },
    "diagonal_spread": {
        "min_level": "advanced",
        "legs": 2,
        "multi_expiry": True,
        "risk": "defined",
        "description": "Different strikes AND different expiries; complex vega + theta interplay.",
    },
    "ratio_spread": {
        "min_level": "advanced",
        "legs": 2,
        "risk": "undefined",
        "description": "Unequal number of long vs short contracts; one side becomes uncovered.",
    },
    "naked_options": {
        "min_level": "advanced",
        "legs": 1,
        "risk": "undefined",
        "description": "Short options with no offsetting position; unlimited loss potential.",
    },
    "advanced_greeks_management": {
        "min_level": "advanced",
        "description": "Delta hedging, vega neutrality, portfolio-level Greeks rebalancing.",
    },
}

# Context-based gates: triggered by *how* the investor is trading,
# regardless of the named strategy. These reflect cognitive complexity,
# not just product complexity.
#
# Format: {condition_key: value_threshold, min_level, reason}
CONTEXT_GATES: list[dict] = [
    # Leg count overrides (same-ticker depth)
    {
        "condition": "leg_count",
        "operator": ">=",
        "threshold": 5,
        "min_level": "advanced",
        "reason": "5+ legs on a single ticker requires real-time Greeks monitoring across all legs.",
    },
    {
        "condition": "leg_count",
        "operator": "between",
        "threshold": (2, 4),
        "min_level": "intermediate",
        "reason": "Multi-leg trades require understanding how legs offset each other's risk.",
    },
    # Concurrent open positions (breadth)
    {
        "condition": "concurrent_positions",
        "operator": ">=",
        "threshold": 5,
        "min_level": "advanced",
        "reason": (
            "5+ open positions requires portfolio-level Greeks aggregation. "
            "Delta from one position can offset another — managing them independently misses this."
        ),
    },
    {
        "condition": "concurrent_positions",
        "operator": "between",
        "threshold": (2, 4),
        "min_level": "intermediate",
        "reason": "Multiple concurrent positions require tracking P&L and expiry dates across positions.",
    },
    # Cross-ticker options activity (portfolio breadth)
    {
        "condition": "cross_ticker_count",
        "operator": ">=",
        "threshold": 4,
        "min_level": "advanced",
        "reason": (
            "Options across 4+ tickers with correlated underlyings (e.g., NVDA + QQQ during a tech move) "
            "amplify each other in ways that require portfolio-level risk management."
        ),
    },
    {
        "condition": "cross_ticker_count",
        "operator": "between",
        "threshold": (2, 3),
        "min_level": "intermediate",
        "reason": "Options on 2–3 tickers simultaneously requires tracking multiple positions and expiries.",
    },
    # Risk type override
    {
        "condition": "is_undefined_risk",
        "operator": "is_true",
        "min_level": "advanced",
        "reason": "Undefined/unlimited risk positions (naked, ratio spreads) require advanced risk management.",
    },
    # Multiple expiration dates
    {
        "condition": "has_multiple_expirations",
        "operator": "is_true",
        "min_level": "advanced",
        "reason": (
            "Trading across multiple expiry dates introduces calendar risk (vega as a primary driver) "
            "which behaves very differently from single-expiry positions."
        ),
    },
]

LEVEL_ORDER: dict[str, int] = {"beginner": 0, "intermediate": 1, "advanced": 2}
