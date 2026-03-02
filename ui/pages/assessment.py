"""
Assessment page: 12-question options knowledge survey.

All questions shown at once, grouped by category. On submit the assessment
agent classifies the investor and stores a structured profile. The results
screen shows level, score, strengths, weaknesses, and unlocked actions.
"""

import streamlit as st

from assessment.agent import run_assessment_agent
from assessment.questions import QUESTIONS

_CATEGORY_LABELS = {
    "fundamental_safety":   "Fundamentals & Risk",
    "strategy_application": "Strategy Application",
    "advanced_risk":        "Advanced Risk",
}

_LEVEL_CONFIG = {
    "beginner":     {"color": "#FF9800", "emoji": "🌱", "label": "Beginner"},
    "intermediate": {"color": "#2196F3", "emoji": "📈", "label": "Intermediate"},
    "advanced":     {"color": "#4CAF50", "emoji": "🎯", "label": "Advanced"},
}


def _badge(level: str) -> str:
    cfg = _LEVEL_CONFIG.get(level, {"color": "gray", "emoji": "?", "label": level})
    return (
        f'<span style="background:{cfg["color"]};color:white;padding:4px 14px;'
        f'border-radius:12px;font-weight:600;font-size:1rem">'
        f'{cfg["emoji"]} {cfg["label"]}</span>'
    )


def _questionnaire() -> None:
    st.title("Options Knowledge Assessment")
    st.markdown(
        "Answer all 12 questions to receive your investor level and a personalised "
        "breakdown of your options knowledge. There are no wrong answers — this "
        "helps calibrate the depth of explanations you'll see throughout the platform."
    )
    st.divider()

    answers = {}
    all_answered = True

    # Group questions by category in order
    categories = list(dict.fromkeys(q["category"] for q in QUESTIONS.values()))

    for cat in categories:
        st.subheader(_CATEGORY_LABELS.get(cat, cat))
        qs = {k: v for k, v in QUESTIONS.items() if v["category"] == cat}

        for qnum, q in qs.items():
            choice_labels = [f"{letter}. {text}" for letter, text in q["choices"].items()]
            choice_keys   = list(q["choices"].keys())

            selection = st.radio(
                label=f"**{qnum}.** {q['text']}",
                options=choice_labels,
                index=None,
                key=f"q{qnum}",
            )
            if selection is None:
                all_answered = False
            else:
                answers[qnum] = choice_keys[choice_labels.index(selection)]

        st.divider()

    submitted = st.button("Submit assessment", type="primary", use_container_width=True)

    if submitted:
        if not all_answered:
            st.warning("Please answer all 12 questions before submitting.")
            return

        with st.spinner("Analysing your answers…"):
            profile = run_assessment_agent(answers)

        st.session_state.investor_profile    = profile
        st.session_state.assessment_answers  = answers
        st.session_state.assessment_complete = True
        st.rerun()


def _results(profile: dict) -> None:
    level = profile.get("level", "beginner")

    st.title("Assessment Results")
    st.markdown(_badge(level), unsafe_allow_html=True)
    st.markdown("")

    col1, col2 = st.columns(2)
    col1.metric("Raw score",      profile.get("raw_score", "—"))
    col2.metric("Weighted score", f"{profile.get('weighted_score_pct', 0):.1f}%")

    st.divider()

    st.subheader("Category breakdown")
    breakdown = profile.get("category_breakdown", {})
    for cat, scores in breakdown.items():
        correct = scores["correct"]
        total   = scores["total"]
        label   = _CATEGORY_LABELS.get(cat, cat)
        st.progress(correct / total, text=f"{label}: {correct}/{total}")

    st.divider()

    if st.button("→ View your portfolio", type="primary"):
        st.switch_page(st.session_state["_pages"]["portfolio"])


def show() -> None:
    if st.session_state.get("assessment_complete") and st.session_state.get("investor_profile"):
        _results(st.session_state.investor_profile)
    else:
        _questionnaire()
