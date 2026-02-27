"""
Static data: the 12-question bank and level-based available actions.

Keeping data here means tools.py and agent.py can import it without
depending on each other, and a future questions v2 only touches this file.
"""

QUESTIONS: dict[int, dict] = {
    1: {
        "text": "What is your maximum loss when buying a call option?",
        "correct": "B",
        "choices": {
            "A": "Unlimited",
            "B": "The premium you paid",
            "C": "The difference between strike and stock price",
            "D": "The current stock price",
        },
        "category": "fundamental_safety",
        "weight": 2,
        "concept": "basic_risk_understanding",
        "concept_label": "Basic Risk: Maximum loss on a long options position",
    },
    2: {
        "text": (
            "You own 200 shares of Apple at $180. You want extra income but are"
            " okay selling at $190. What should you do?"
        ),
        "correct": "B",
        "choices": {
            "A": "Buy Apple call options",
            "B": "Sell 2 Apple $190 covered calls",
            "C": "Buy Apple put options",
            "D": "Sell Apple and buy options instead",
        },
        "category": "fundamental_safety",
        "weight": 2,
        "concept": "covered_call_strategy",
        "concept_label": "Covered Calls: Income generation from existing holdings",
    },
    3: {
        "text": "Options lose value as expiration approaches due to:",
        "correct": "B",
        "choices": {
            "A": "Delta decay",
            "B": "Time decay (Theta)",
            "C": "Volatility changes",
            "D": "Interest rate changes",
        },
        "category": "fundamental_safety",
        "weight": 2,
        "concept": "time_decay_theta",
        "concept_label": "Time Decay (Theta): How options lose value over time",
    },
    4: {
        "text": (
            "The market is volatile and you're worried about your $10,000 tech"
            " portfolio dropping 20%. You want protection but don't want to sell."
            " What's your best option?"
        ),
        "correct": "B",
        "choices": {
            "A": "Buy more tech stocks when they drop",
            "B": "Buy protective puts on your holdings",
            "C": "Sell covered calls",
            "D": "Move everything to cash",
        },
        "category": "fundamental_safety",
        "weight": 2,
        "concept": "portfolio_protection",
        "concept_label": "Portfolio Protection: Using puts to hedge downside risk",
    },
    5: {
        "text": "Delta measures:",
        "correct": "B",
        "choices": {
            "A": "Time until expiration",
            "B": "How much option price changes when stock moves $1",
            "C": "Volatility sensitivity",
            "D": "Interest rate sensitivity",
        },
        "category": "strategy_application",
        "weight": 1.5,
        "concept": "delta_greek",
        "concept_label": "Delta: Option price sensitivity to underlying movement",
    },
    6: {
        "text": (
            "You sold a covered call on Microsoft for $3 premium with a $320 strike."
            " Microsoft is now at $325 and expires tomorrow. What should you expect?"
        ),
        "correct": "B",
        "choices": {
            "A": "Keep the stock and the $3",
            "B": "Your shares will likely be called away, but you keep the $3",
            "C": "You'll lose money",
            "D": "Nothing happens automatically",
        },
        "category": "strategy_application",
        "weight": 1.5,
        "concept": "assignment_mechanics",
        "concept_label": "Assignment Mechanics: What happens when ITM options expire",
    },
    7: {
        "text": (
            "You want to buy Netflix at $400 (currently $420). You have $40,000 cash."
            " Which strategy makes sense?"
        ),
        "correct": "C",
        "choices": {
            "A": "Wait for the stock to hit $400",
            "B": "Buy Netflix call options",
            "C": "Sell Netflix $400 cash-secured puts",
            "D": "Buy Netflix stock now",
        },
        "category": "strategy_application",
        "weight": 1.5,
        "concept": "cash_secured_puts",
        "concept_label": "Cash-Secured Puts: Strategic entry at target price with premium income",
    },
    8: {
        "text": "Before earnings announcements, option prices typically:",
        "correct": "B",
        "choices": {
            "A": "Decrease due to uncertainty",
            "B": "Increase due to higher implied volatility",
            "C": "Stay the same",
            "D": "Only change if earnings are good",
        },
        "category": "strategy_application",
        "weight": 1.5,
        "concept": "implied_volatility_earnings",
        "concept_label": "Implied Volatility: Pre-earnings IV expansion",
    },
    9: {
        "text": (
            "You have 5 different options positions expiring next week, all slightly"
            " in-the-money. Your priority should be:"
        ),
        "correct": "C",
        "choices": {
            "A": "Let them all expire automatically",
            "B": "Close the most profitable ones first",
            "C": "Manage each based on assignment risk and portfolio impact",
            "D": "Roll them all to next month",
        },
        "category": "advanced_risk",
        "weight": 1,
        "concept": "expiration_management",
        "concept_label": "Expiration Management: Actively managing ITM positions near expiry",
    },
    10: {
        "text": "High 'gamma' exposure in your portfolio means:",
        "correct": "B",
        "choices": {
            "A": "Your positions decay slowly",
            "B": "Small stock moves can cause large option value swings",
            "C": "You're protected against volatility",
            "D": "Your positions are very safe",
        },
        "category": "advanced_risk",
        "weight": 1,
        "concept": "gamma_exposure",
        "concept_label": "Gamma: Accelerating delta sensitivity near expiry",
    },
    11: {
        "text": (
            "You're considering an iron condor on SPY (sell $430/$450 calls, sell"
            " $400/$380 puts, SPY at $425). This strategy profits if:"
        ),
        "correct": "B",
        "choices": {
            "A": "SPY moves strongly in either direction",
            "B": "SPY stays between $400-$450",
            "C": "SPY only goes up",
            "D": "Volatility increases dramatically",
        },
        "category": "advanced_risk",
        "weight": 1,
        "concept": "multi_leg_strategies",
        "concept_label": "Multi-Leg Strategies: Iron condor mechanics and profit conditions",
    },
    12: {
        "text": (
            "After Tesla earnings, your Tesla call options lost 30% overnight despite"
            " the stock only dropping 5%. This is most likely due to:"
        ),
        "correct": "B",
        "choices": {
            "A": "Time decay acceleration",
            "B": "Implied volatility crush",
            "C": "Interest rate changes",
            "D": "Trading volume decrease",
        },
        "category": "advanced_risk",
        "weight": 1,
        "concept": "volatility_crush",
        "concept_label": "Volatility Crush: Post-earnings IV collapse and its impact on options",
    },
}

AVAILABLE_ACTIONS: dict[str, list[str]] = {
    "beginner": [
        "Educational option chains with explanations",
        "Buy calls/puts (max 2% position size)",
        "Paper trading strongly recommended",
        "Complete Options 101 course",
        "Greeks explanations with every position",
    ],
    "intermediate": [
        "All beginner features",
        "Covered calls on your existing stocks",
        "Protective puts for portfolio protection",
        "Cash-secured puts for strategic entry",
        "Real-time P&L with your actual tickers",
        "Position sizes up to 10% of portfolio",
        "Real-time Greeks and P&L attribution",
        "Multi-leg strategies (limited to 2 legs)",
    ],
    "advanced": [
        "All intermediate features",
        "Complex multi-leg strategies (iron condors, spreads, butterflies)",
        "Advanced Greeks-based portfolio management",
        "Higher position limits with appropriate safeguards",
        "Advanced risk analytics and scenario planning",
        "Naked options (with additional warnings)",
        "Advanced portfolio analytics",
    ],
}
