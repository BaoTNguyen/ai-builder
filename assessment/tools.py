"""
Tool implementations and schemas for the assessment agent.

Five tools in total:
  Assessment pipeline (3 tools, run once per survey):
    1. analyze_all_answers    — scores + per-question analysis in one call
    2. classify_level         — applies the weighted classification algorithm
    3. store_investor_profile — persists the final JSON

  Action gate (2 tools, called when a user attempts a feature):
    4. check_action_permission  — returns allowed/blocked + alternatives
    5. create_paper_portfolio   — mirrors the real portfolio for paper trading

Keeping implementations here (separate from agent.py) means they can be
unit-tested independently and reused by other agents later.
"""

import json
from datetime import datetime
from pathlib import Path

from assessment.questions import QUESTIONS
from core.gates import STRATEGY_GATES, CONTEXT_GATES, LEVEL_ORDER
from portfolio.positions import PORTFOLIO


# ─── Implementations ──────────────────────────────────────────────────────────

def analyze_all_answers(answers: dict) -> dict:
    """
    Processes all 12 answers in one shot.

    For each question returns: the question text, selected choice text, correct
    choice text, whether it's correct, the concept tested, the scoring weight,
    and the full choices dict so the agent can reason about specific misconceptions.

    Also computes weighted scores and per-category breakdowns so classify_level
    has everything it needs without an extra round-trip.
    """
    question_results = []
    total_weight = 0.0
    earned_weight = 0.0
    category_breakdown: dict[str, dict] = {
        "fundamental_safety":   {"correct": 0, "total": 0},
        "strategy_application": {"correct": 0, "total": 0},
        "advanced_risk":        {"correct": 0, "total": 0},
    }
    raw_correct = 0

    for qid_str, answer in answers.items():
        q = QUESTIONS[int(qid_str)]
        selected = answer.upper()
        is_correct = selected == q["correct"]
        weight = q["weight"]
        cat = q["category"]

        total_weight += weight
        category_breakdown[cat]["total"] += 1
        if is_correct:
            earned_weight += weight
            category_breakdown[cat]["correct"] += 1
            raw_correct += 1

        question_results.append({
            "question_id": int(qid_str),
            "question": q["text"],
            "selected": selected,
            "selected_text": q["choices"].get(selected, "Unknown choice"),
            "correct_answer": q["correct"],
            "correct_text": q["choices"][q["correct"]],
            "is_correct": is_correct,
            "category": cat,
            "weight": weight,
            "concept": q["concept"],
            "concept_label": q["concept_label"],
            "all_choices": q["choices"],
        })

    weighted_pct = round((earned_weight / total_weight) * 100, 1) if total_weight else 0.0

    return {
        "question_results": question_results,
        "score": {
            "raw_correct": raw_correct,
            "total_questions": len(answers),
            "weighted_score_pct": weighted_pct,
            "category_breakdown": category_breakdown,
        },
    }


def classify_level(score: dict) -> dict:
    """
    Applies the classification algorithm from compressed_options_assessment.md:
      Advanced:     fundamentals ≥3, strategy ≥3, advanced ≥3, weighted >75%
      Intermediate: fundamentals ≥3, strategy ≥2, 50% ≤ weighted ≤ 75%
      Beginner:     everything else
    """
    cb = score["category_breakdown"]
    fs = cb["fundamental_safety"]["correct"]
    sa = cb["strategy_application"]["correct"]
    ar = cb["advanced_risk"]["correct"]
    pct = score["weighted_score_pct"]
    raw = score["raw_correct"]

    if fs >= 3 and sa >= 3 and ar >= 3 and pct > 75:
        level = "advanced"
    elif fs >= 3 and sa >= 2 and 50 <= pct <= 75:
        level = "intermediate"
    else:
        level = "beginner"

    return {
        "level": level,
        "raw_score": f"{raw}/12",
        "weighted_score_pct": pct,
    }


def store_investor_profile(profile: dict, filepath: str = "profiles/investor_profile.json") -> dict:
    """Persists the investor profile as a JSON file."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    profile["generated_at"] = datetime.now().isoformat()
    with open(filepath, "w") as f:
        json.dump(profile, f, indent=2)
    return {"success": True, "filepath": filepath}


# ─── Action Gate ──────────────────────────────────────────────────────────────

def _evaluate_context_gates(context: dict) -> str | None:
    """
    Evaluates context-based gates and returns the highest required level
    triggered by the context, or None if no context gate fires.

    Context keys:
      leg_count             (int)  — legs in the attempted trade
      concurrent_positions  (int)  — currently open options positions
      cross_ticker_count    (int)  — distinct tickers with options activity
      is_undefined_risk     (bool) — position carries unlimited loss potential
      has_multiple_expirations (bool) — trade spans more than one expiry date
    """
    if not context:
        return None

    highest_rank = -1
    highest_level = None

    for gate in CONTEXT_GATES:
        key = gate["condition"]
        val = context.get(key)
        if val is None:
            continue

        fired = False
        op = gate["operator"]

        if op == ">=" and isinstance(val, (int, float)):
            fired = val >= gate["threshold"]
        elif op == "between" and isinstance(val, (int, float)):
            lo, hi = gate["threshold"]
            fired = lo <= val <= hi
        elif op == "is_true":
            fired = bool(val)

        if fired:
            level = gate["min_level"]
            rank = LEVEL_ORDER[level]
            if rank > highest_rank:
                highest_rank = rank
                highest_level = level

    return highest_level


def check_action_permission(
    attempted_action: str,
    investor_level: str,
    context: dict | None = None,
) -> dict:
    """
    Two-layer gate: checks the named strategy requirement AND any context-based
    signals, then returns the stricter of the two.

    Args:
        attempted_action: strategy key from STRATEGY_GATES, e.g. 'iron_condor'
        investor_level:   'beginner' | 'intermediate' | 'advanced'
        context:          optional trading context signals —
                            leg_count             (int)
                            concurrent_positions  (int)
                            cross_ticker_count    (int)
                            is_undefined_risk     (bool)
                            has_multiple_expirations (bool)

    Returns dict with:
        allowed             bool
        required_level      str   (strictest gate that fired)
        blocking_reasons    list  (which gates fired and why)
        alternatives        list  (if blocked: learn_more, take_assessment, paper_trading)
    """
    investor_rank = LEVEL_ORDER.get(investor_level, 0)

    # Layer 1: named strategy gate
    strategy_gate = STRATEGY_GATES.get(attempted_action)
    strategy_level = strategy_gate["min_level"] if strategy_gate else "advanced"
    strategy_rank = LEVEL_ORDER[strategy_level]

    # Layer 2: context-based gates
    context_level = _evaluate_context_gates(context)
    context_rank = LEVEL_ORDER[context_level] if context_level else -1

    # Take the stricter of the two
    required_rank = max(strategy_rank, context_rank)
    required_level = [k for k, v in LEVEL_ORDER.items() if v == required_rank][0]
    allowed = investor_rank >= required_rank

    if allowed:
        return {
            "allowed": True,
            "attempted_action": attempted_action,
            "investor_level": investor_level,
            "required_level": required_level,
        }

    # Build blocking reasons so the UI can explain *why*
    blocking_reasons = []
    if strategy_rank > investor_rank:
        desc = strategy_gate.get("description", "") if strategy_gate else ""
        blocking_reasons.append({
            "gate": "strategy",
            "reason": f"'{attempted_action}' requires {strategy_level} level. {desc}",
        })
    if context_level and context_rank > investor_rank:
        for gate in CONTEXT_GATES:
            key = gate["condition"]
            val = (context or {}).get(key)
            if val is None:
                continue
            op = gate["operator"]
            threshold = gate.get("threshold")
            if op == ">=" and isinstance(val, (int, float)):
                fired = val >= threshold
            elif op == "between" and isinstance(val, (int, float)):
                lo, hi = threshold
                fired = lo <= val <= hi
            elif op == "is_true":
                fired = bool(val)
            else:
                fired = False

            if fired and LEVEL_ORDER[gate["min_level"]] > investor_rank:
                blocking_reasons.append({
                    "gate": f"context:{key}",
                    "reason": gate["reason"],
                })

    next_level = {0: "intermediate", 1: "advanced"}.get(investor_rank, "advanced")
    action_label = attempted_action.replace("_", " ")

    return {
        "allowed": False,
        "attempted_action": attempted_action,
        "investor_level": investor_level,
        "required_level": required_level,
        "blocking_reasons": blocking_reasons,
        "alternatives": [
            {
                "action": "learn_more",
                "label": f"Learn about {action_label}",
                "description": (
                    f"Read a guide on how {action_label} works, "
                    f"including real examples and risk/reward tradeoffs."
                ),
            },
            {
                "action": "take_assessment",
                "label": f"Take the {next_level} assessment to unlock this",
                "description": (
                    f"Complete the {next_level}-level knowledge assessment. "
                    f"It takes ~5 minutes and unlocks {action_label} "
                    f"along with other {next_level} features."
                ),
            },
            {
                "action": "paper_trading",
                "label": "Try it in paper trading first",
                "description": (
                    "Practice this strategy on an exact replica of your portfolio "
                    "with no real money at risk. Your paper portfolio mirrors your "
                    "current holdings at today's prices."
                ),
            },
        ],
    }


def create_paper_portfolio(
    investor_profile: dict,
    attempted_action: str,
    filepath: str | None = None,
) -> dict:
    """
    Creates a paper trading portfolio that mirrors the investor's real holdings.

    Each position is cloned at its current price with an empty paper_trades list.
    The investor can then practice the attempted_action without risking real capital.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if filepath is None:
        filepath = f"profiles/paper_{timestamp}.json"

    all_positions = PORTFOLIO["etfs"] + PORTFOLIO["stocks"]
    paper_positions = [
        {
            "ticker": p["ticker"],
            "name": p["name"],
            "asset_type": "etf" if p in PORTFOLIO["etfs"] else "stock",
            "shares": p["shares"],
            "entry_price": p["price"],       # locked at creation time
            "current_price": p["price"],     # updated by paper trading UI
            "market_value": p["market_value"],
            "unrealized_pnl": 0.0,
            "contracts_available": p["contracts_available"],
            "paper_trades": [],              # options trades the user opens in paper mode
        }
        for p in all_positions
    ]

    paper_portfolio = {
        "paper_portfolio_id": f"paper_{timestamp}",
        "created_at": datetime.now().isoformat(),
        "investor_level": investor_profile.get("level", "beginner"),
        "attempted_action": attempted_action,
        "mode": "paper_trading",
        "total_value": PORTFOLIO["total_value"],
        "cash_available": 0.0,
        "paper_pnl": 0.0,
        "positions": paper_positions,
        "note": (
            f"This is a paper trading portfolio. It mirrors your real holdings at "
            f"today's prices. Practice '{attempted_action.replace('_', ' ')}' here "
            f"before using real capital."
        ),
    }

    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(paper_portfolio, f, indent=2)

    return {"success": True, "filepath": filepath, "paper_portfolio": paper_portfolio}


# ─── Dispatch ─────────────────────────────────────────────────────────────────

def dispatch(name: str, tool_input: dict) -> str:
    if name == "analyze_all_answers":
        result = analyze_all_answers(tool_input["answers"])
    elif name == "classify_level":
        result = classify_level(tool_input["score"])
    elif name == "store_investor_profile":
        result = store_investor_profile(
            tool_input["profile"],
            tool_input.get("filepath", "profiles/investor_profile.json"),
        )
    elif name == "check_action_permission":
        result = check_action_permission(
            tool_input["attempted_action"],
            tool_input["investor_level"],
        )
    elif name == "create_paper_portfolio":
        result = create_paper_portfolio(
            tool_input["investor_profile"],
            tool_input["attempted_action"],
            tool_input.get("filepath"),
        )
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


# ─── Schemas ──────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "analyze_all_answers",
        "description": (
            "Process all 12 survey answers in one call. Returns per-question analysis "
            "(selected text, correct text, concept, whether correct) plus weighted scores "
            "and category breakdowns. Call this first and once — all answers are available "
            "upfront since the survey is completed on a single page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "answers": {
                    "type": "object",
                    "description": (
                        'All 12 answers as {"question_id": "letter"}, '
                        'e.g. {"1": "B", "2": "A", ..., "12": "C"}'
                    ),
                    "additionalProperties": {"type": "string"},
                }
            },
            "required": ["answers"],
        },
    },
    {
        "name": "classify_level",
        "description": (
            "Classify the investor as beginner, intermediate, or advanced. "
            "Pass the 'score' object returned inside analyze_all_answers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "score": {
                    "type": "object",
                    "description": "The 'score' dict from analyze_all_answers.",
                }
            },
            "required": ["score"],
        },
    },
    {
        "name": "store_investor_profile",
        "description": "Persist the final investor profile JSON to disk.",
        "input_schema": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "object",
                    "description": (
                        "Complete profile with: level, raw_score, weighted_score_pct, "
                        "category_breakdown, strengths, weaknesses, available_actions."
                    ),
                },
                "filepath": {
                    "type": "string",
                    "description": "Output path (default: profiles/investor_profile.json).",
                },
            },
            "required": ["profile"],
        },
    },
    {
        "name": "check_action_permission",
        "description": (
            "Two-layer gate: checks the named strategy requirement AND optional "
            "context signals (leg count, concurrent positions, cross-ticker breadth, "
            "risk type, multiple expiries). Returns the stricter of the two, with "
            "specific blocking reasons and three alternatives if blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "attempted_action": {
                    "type": "string",
                    "description": (
                        "Strategy key, e.g. 'covered_calls', 'iron_condor', "
                        "'vertical_spread', 'naked_options', 'calendar_spread'."
                    ),
                },
                "investor_level": {
                    "type": "string",
                    "enum": ["beginner", "intermediate", "advanced"],
                },
                "context": {
                    "type": "object",
                    "description": (
                        "Optional trading context for context-based gate evaluation. "
                        "leg_count (int): legs in this trade. "
                        "concurrent_positions (int): currently open options positions. "
                        "cross_ticker_count (int): distinct tickers with options activity. "
                        "is_undefined_risk (bool): position has unlimited loss potential. "
                        "has_multiple_expirations (bool): trade spans multiple expiry dates."
                    ),
                    "properties": {
                        "leg_count":               {"type": "integer"},
                        "concurrent_positions":    {"type": "integer"},
                        "cross_ticker_count":      {"type": "integer"},
                        "is_undefined_risk":       {"type": "boolean"},
                        "has_multiple_expirations":{"type": "boolean"},
                    },
                },
            },
            "required": ["attempted_action", "investor_level"],
        },
    },
    {
        "name": "create_paper_portfolio",
        "description": (
            "Create a paper trading portfolio that exactly mirrors the investor's "
            "real holdings at current prices. Call this when the user selects the "
            "paper_trading alternative from check_action_permission."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "investor_profile": {
                    "type": "object",
                    "description": "The investor's profile dict (needs 'level' field).",
                },
                "attempted_action": {
                    "type": "string",
                    "description": "The action they want to practice in paper mode.",
                },
                "filepath": {
                    "type": "string",
                    "description": "Output path (auto-generated with timestamp if omitted).",
                },
            },
            "required": ["investor_profile", "attempted_action"],
        },
    },
]
