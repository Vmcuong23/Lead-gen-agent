"""
FDI Agent dashboard.

Local: `streamlit run streamlit_app.py`
Cloud: deploy this repo to Streamlit Cloud, point at `streamlit_app.py`.

Configuration precedence (highest first):
  1. Streamlit secrets (st.secrets["DATABASE_URL"], etc.) — Cloud deployments
  2. Environment variables — local / Docker / systemd
  3. Defaults — local dev fallback
"""
from __future__ import annotations

import os
import traceback

import streamlit as st

st.set_page_config(
    page_title="FDI Agent — Vietnam IT decision-makers",
    page_icon="🏭",
    layout="wide",
)


# Pull Streamlit Cloud secrets into env so SQLAlchemy etc. find them
try:
    for key in (
        "DATABASE_URL",
        "ANTHROPIC_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "VOYAGE_API_KEY",
        "ANTHROPIC_MODEL",
    ):
        if key in st.secrets and not os.environ.get(key):
            os.environ[key] = str(st.secrets[key])
except Exception:
    pass


st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    section[data-testid="stSidebar"] .block-container { padding-top: 1rem; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _db_status() -> tuple[bool, str]:
    if not os.environ.get("DATABASE_URL"):
        return False, "DATABASE_URL is not set"
    try:
        from sqlalchemy import text
        from agent1_schema.models.db import session_scope

        with session_scope() as s:
            s.execute(text("SELECT 1"))
        return True, "connected"
    except Exception as e:
        return False, str(e)[:200]


with st.sidebar:
    st.title("🏭 FDI Agent")
    st.caption("Personal intelligence agent for Vietnam FDI sales.")
    page = st.radio(
        "Navigate",
        ["Overview", "Companies", "People", "Daily runs", "Run jobs", "Export"],
        label_visibility="collapsed",
    )
    st.divider()

    ok, msg = _db_status()
    if ok:
        st.success("DB connected")
    else:
        st.error(f"DB unavailable\n\n{msg}")
        st.caption(
            "Set DATABASE_URL in Streamlit secrets or environment. "
            "Example: postgresql+psycopg://user:pwd@host:5432/dbname"
        )

    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        safe = db_url.split("@")[-1][:50]
        st.caption(f"`…@{safe}`")


def _safe_render(module_path: str) -> None:
    try:
        if not _db_status()[0]:
            st.warning(
                "This page needs a database connection. Configure DATABASE_URL "
                "in Streamlit secrets or the environment, then refresh."
            )
            st.code(
                'DATABASE_URL = "postgresql+psycopg://user:pwd@host:5432/fdi_agent"',
                language="toml",
            )
            return
        mod = __import__(module_path, fromlist=["render"])
        mod.render()
    except Exception:
        st.error("This page crashed. Stacktrace below.")
        st.code(traceback.format_exc(), language="text")


if page == "Overview":
    _safe_render("interface.pages.overview")
elif page == "Companies":
    _safe_render("interface.pages.companies")
elif page == "People":
    _safe_render("interface.pages.people")
elif page == "Daily runs":
    _safe_render("interface.pages.runs")
elif page == "Run jobs":
    _safe_render("interface.pages.jobs")
elif page == "Export":
    _safe_render("interface.pages.export")
