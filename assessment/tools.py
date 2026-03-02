"""
Tool implementations and schemas for the assessment agent.

Five tools in total:
  Assessment pipeline (3 tools, run once per survey):
    1. analyze_all_answers    — scores + per-question analysis in one call
    2. classify_level         — applies the weighted classification algorithm
    3. store_investor_profile — persists the final JSON

  Action gate (2 tools, called when a user attempts a feature):
    4. check_action_permission  — returns a notification (advisory | warning | null)
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

def _highest_context_level(context: dict) -> str | None:
    """
    Returns the highest level required by any context gate that fires,
    or None if none fire. Skips is_undefined_risk — that is handled
    separately as a warning trigger, not a level gap signal.
    """
    if not context:
        return None

    highest_rank = -1
    highest_level = None

    for gate in CONTEXT_GATES:
        if gate["condition"] == "is_undefined_risk":
            continue  # handled separately as a warning, not an advisory gap

        key = gate["condition"]
        val = context.get(key)
        if val is None:
            continue

        op = gate["operator"]
        fired = False
        if op == ">=" and isinstance(val, (int, float)):
            fired = val >= gate["threshold"]
        elif op == "between" and isinstance(val, (int, float)):
            lo, hi = gate["threshold"]
            fired = lo <= val <= hi
        elif op == "is_true":
            fired = bool(val)

        if fired:
            rank = LEVEL_ORDER[gate["min_level"]]
            if rank > highest_rank:
                highest_rank = rank
                highest_level = gate["min_level"]

    return highest_level


def _build_suggestions(action_label: str, investor_rank: int) -> list:
    next_level = {0: "intermediate", 1: "advanced"}.get(investor_rank, "advanced")
    return [
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
            "label": f"Take the {next_level} assessment",
            "description": (
                f"Complete the {next_level}-level knowledge check (~5 minutes). "
                f"It gives you a clearer picture of where your gaps are."
            ),
        },
        {
            "action": "paper_trading",
            "label": "Try it in paper trading first",
            "description": (
                "Practice this strategy on an exact replica of your portfolio "
                "with no real money at risk."
            ),
        },
    ]


def check_action_permission(
    attempted_action: str,
    investor_level: str,
    context: dict | None = None,
) -> dict:
    """
    Evaluates a strategy attempt and returns a notification level — or none.

    Two notification levels (never a block):
      "warning"  — strategy or context signals unlimited/undefined risk
                   (naked options, ratio spreads, is_undefined_risk=True).
                   Shown regardless of investor level.
      "advisory" — strategy or context requires a higher level than assessed.
                   Explains the gap and suggests next steps, but does not
                   prevent the action.
      null       — no friction; investor is assessed at the appropriate level
                   and no risk flags apply.

    Args:
        attempted_action: strategy key from STRATEGY_GATES, e.g. 'iron_condor'
        investor_level:   'beginner' | 'intermediate' | 'advanced'
        context:          optional trading context —
                            leg_count             (int)
                            concurrent_positions  (int)
                            cross_ticker_count    (int)
                            is_undefined_risk     (bool)
                            has_multiple_expirations (bool)

    Returns dict with:
        attempted_action  str
        investor_level    str
        notification      dict | None
          level           "advisory" | "warning"
          headline        str
          risks           list[str]   (warning only)
          gaps            list[str]   (advisory only — which knowledge areas to build)
          suggestions     list[dict]  (learn_more, take_assessment, paper_trading)
    """
    ctx = context or {}
    investor_rank = LEVEL_ORDER.get(investor_level, 0)
    action_label = attempted_action.replace("_", " ")

    strategy_gate = STRATEGY_GATES.get(attempted_action, {})
    strategy_level = strategy_gate.get("min_level", "advanced")
    strategy_rank = LEVEL_ORDER[strategy_level]

    # ── Warning: undefined/unlimited risk ─────────────────────────────────────
    is_undefined = (
        strategy_gate.get("risk") == "undefined"
        or ctx.get("is_undefined_risk", False)
    )

    if is_undefined:
        risks = [
            "This strategy carries unlimited or very large loss potential — "
            "losses are not capped by a premium paid.",
            "Margin calls can force position closure at an unfavourable price.",
            "Volatile underlyings can gap through your strike overnight.",
        ]
        if strategy_gate.get("risk") == "undefined":
            risks.append(strategy_gate.get("description", ""))

        return {
            "attempted_action": attempted_action,
            "investor_level": investor_level,
            "notification": {
                "level": "warning",
                "headline": f"{action_label.title()} carries unlimited risk",
                "risks": [r for r in risks if r],
                "suggestions": _build_suggestions(action_label, investor_rank),
            },
        }

    # ── Advisory: assessed level below what this strategy typically requires ──
    context_level = _highest_context_level(ctx)
    context_rank = LEVEL_ORDER[context_level] if context_level else -1
    required_rank = max(strategy_rank, context_rank)
    required_level = [k for k, v in LEVEL_ORDER.items() if v == required_rank][0]

    has_gap = investor_rank < required_rank

    if has_gap:
        gaps = []
        if strategy_rank > investor_rank:
            desc = strategy_gate.get("description", "")
            gaps.append(
                f"{action_label.title()} is typically used by {strategy_level} investors. "
                + (desc if desc else "")
            )
        if context_level and context_rank > investor_rank:
            for gate in CONTEXT_GATES:
                if gate["condition"] == "is_undefined_risk":
                    continue
                key = gate["condition"]
                val = ctx.get(key)
                if val is None:
                    continue
                op, threshold = gate["operator"], gate.get("threshold")
                if op == ">=" and isinstance(val, (int, float)):
                    fired = val >= threshold
                elif op == "between" and isinstance(val, (int, float)):
                    lo, hi = threshold
                    fired = lo <= val <= hi
                else:
                    fired = False
                if fired and LEVEL_ORDER[gate["min_level"]] > investor_rank:
                    gaps.append(gate["reason"])

        return {
            "attempted_action": attempted_action,
            "investor_level": investor_level,
            "notification": {
                "level": "advisory",
                "headline": (
                    f"This is typically a {required_level}-level strategy — "
                    f"here's what to keep in mind"
                ),
                "gaps": gaps,
                "suggestions": _build_suggestions(action_label, investor_rank),
            },
        }

    # ── No friction ───────────────────────────────────────────────────────────
    return {
        "attempted_action": attempted_action,
        "investor_level": investor_level,
        "notification": None,
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
            tool_input.get("context"),
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
            "Evaluates a strategy attempt and returns a notification level. "
            "'warning' if the strategy carries undefined/unlimited risk (naked options, "
            "ratio spreads, or is_undefined_risk=True in context). "
            "'advisory' if the strategy or context signals require a higher assessed "
            "level than the investor currently has — explains the gap and suggests "
            "next steps without preventing the action. "
            "null if no friction applies."
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
            "paper_trading suggestion from check_action_permission."
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
