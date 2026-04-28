"""
Streamlit Cloud entry point.

Streamlit Cloud and most managed Streamlit hosts look for `streamlit_app.py`
at the repo root and `streamlit run` it. This file:

  1. Ensures the repo root is on sys.path so `from interface import ...`,
     `from agent1_schema import ...`, etc. work without a pip install.
  2. Delegates execution to interface/dashboard.py.

Local development: `streamlit run streamlit_app.py`
"""
import sys
from pathlib import Path

# The repo root is this file's directory. Putting it on sys.path lets
# `from interface import dashboard` resolve to interface/dashboard.py.
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing the dashboard module is what renders the page — Streamlit
# scripts execute top-to-bottom, and dashboard.py contains the st.* calls.
from interface import dashboard  # noqa: F401, E402
