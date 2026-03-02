"""
Wealthsimple Options Demo — Streamlit entry point.

Run with:
    streamlit run app.py
"""

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import streamlit as st

from ui.pages import assessment, portfolio, hypothetical

st.set_page_config(
    page_title="Wealthsimple Options",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Session state defaults ────────────────────────────────────────────────────
_defaults = {
    "assessment_complete": False,
    "investor_profile":    None,
    "assessment_answers":  {},
    "hyp_positions":       [],
    "live_prices":         {},
}
for key, val in _defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ── Navigation ────────────────────────────────────────────────────────────────
pages = {
    "assessment":   st.Page(assessment.show,   title="Assessment",           icon="📋", url_path="assessment"),
    "portfolio":    st.Page(portfolio.show,     title="Portfolio",            icon="📊", url_path="portfolio"),
    "hypothetical": st.Page(hypothetical.show,  title="Position Builder", icon="🔬", url_path="hypothetical"),
}

# Store Page objects so any page can call st.switch_page
st.session_state["_pages"] = pages

pg = st.navigation(list(pages.values()))
pg.run()
