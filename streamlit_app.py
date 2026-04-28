"""
Streamlit Cloud entry point.

Streamlit Cloud, Hugging Face Spaces, and most managed Streamlit hosts
look for `streamlit_app.py` at the repo root. This file is a thin shim
that just delegates to interface/dashboard.py.

Local development: `streamlit run streamlit_app.py`
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Make sub-packages importable as top-level
for pkg in ["agent1_schema", "agent2_fdi_scraper",
             "agent3_people_discovery", "shared", "interface"]:
    p = ROOT / pkg
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Run the dashboard — the import itself is the Streamlit entry point
from interface import dashboard  # noqa: F401, E402
