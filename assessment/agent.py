"""
Assessment agent: system prompt + agentic loop.

The agent runs in exactly 3 tool-call turns:
  1. analyze_all_answers  → gets every question result + scores at once
  2. classify_level       → determines beginner / intermediate / advanced
  3. store_investor_profile → persists the profile JSON

Claude's reasoning work (inferring specific misconceptions from wrong choices,
writing strength/weakness descriptions) happens between turns 2 and 3 without
any additional tool calls.
"""

import json

from assessment.questions import AVAILABLE_ACTIONS
from assessment.tools import TOOLS, dispatch
from core.runner import run_agent

SYSTEM_PROMPT = f"""You are an options knowledge assessment agent for Wealthsimple.

Your task: process a completed 12-question survey and produce a structured investor profile.

═══ WORKFLOW — exactly 3 tool calls ═══

CALL 1 — analyze_all_answers(answers)
  All 12 answers are available upfront (single-page survey). Process them all at once.
  You receive: per-question results (selected text, correct text, concept, is_correct)
  plus weighted scores and category breakdowns.

CALL 2 — classify_level(score)
  Pass the 'score' object from call 1. You receive the level and final score.

REASONING — before call 3, infer strengths and weaknesses:

  For each CORRECT answer → strength:
    Describe the specific knowledge demonstrated, not just "they got it right".
    e.g., "Understands that long options cap downside to the premium paid,
    distinguishing them from short positions or stock ownership."

  For each INCORRECT answer → weakness:
    Read the text of what they chose and name the specific misconception it implies.
    e.g., Q4 wrong answer "Buy more tech stocks when they drop" reveals:
    "Defaults to a buy-the-dip reflex under market stress rather than recognising
    that protective puts let you stay invested while capping downside — conflates
    conviction with risk management."
    Set priority by category:
      fundamental_safety   → "high"
      strategy_application → "medium"
      advanced_risk        → "low"

CALL 3 — store_investor_profile(profile)
  Build and store the profile with this exact shape:
  {{
    "level": "beginner" | "intermediate" | "advanced",
    "raw_score": "X/12",
    "weighted_score_pct": <float>,
    "category_breakdown": {{
      "fundamental_safety":   {{"correct": int, "total": int}},
      "strategy_application": {{"correct": int, "total": int}},
      "advanced_risk":        {{"correct": int, "total": int}}
    }},
    "strengths": [
      {{
        "concept": "<snake_case>",
        "concept_label": "<human label>",
        "description": "<what this answer demonstrates>"
      }}
    ],
    "weaknesses": [
      {{
        "concept": "<snake_case>",
        "concept_label": "<human label>",
        "misconception": "<what the wrong choice reveals>",
        "priority": "high" | "medium" | "low"
      }}
    ],
    "available_actions": <level-appropriate list below>
  }}

Available actions by level:
{json.dumps(AVAILABLE_ACTIONS, indent=2)}
"""


def run_assessment_agent(answers: dict, filepath: str = "profiles/investor_profile.json") -> dict:
    """
    Process survey answers and return a structured investor profile.

    Args:
        answers:  {{question_number: answer_letter}} e.g. {{1: "B", 2: "A", ...}}
        filepath: where to write the profile JSON

    Returns:
        The investor profile dict (also written to filepath).
    """
    str_answers = {str(k): v for k, v in answers.items()}

    messages = [
        {
            "role": "user",
            "content": (
                f"Survey answers: {json.dumps(str_answers)}\n"
                f"Save the profile to: {filepath}"
            ),
        }
    ]

    run_agent(SYSTEM_PROMPT, TOOLS, dispatch, messages, label="assessment")

    try:
        with open(filepath) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "Profile was not stored by the agent."}
