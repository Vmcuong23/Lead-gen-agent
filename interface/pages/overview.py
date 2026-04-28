"""Overview page — top-level KPIs."""
from __future__ import annotations

import streamlit as st

from interface.utils import overview_metrics, role_breakdown


def render() -> None:
    st.title("Overview")
    st.caption("Pipeline health and ICP funnel.")

    m = overview_metrics()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active companies", f"{m['companies']:,}")
    c2.metric("Hot prospects (≥75)", f"{m['companies_hot']:,}")
    c3.metric("People discovered", f"{m['people']:,}")
    c4.metric("Verified emails", f"{m['people_smtp_verified']:,}")

    c5, c6, c7 = st.columns(3)
    c5.metric("Events recorded", f"{m['events']:,}")
    c6.metric("Raw documents", f"{m['raw_docs']:,}")
    coverage = (
        round(100 * m["people_with_email"] / m["people"]) if m["people"] else 0
    )
    c7.metric("Email coverage", f"{coverage}%")

    st.divider()
    st.subheader("People by role")
    df = role_breakdown()
    if df.empty:
        st.info("No people in the database yet. Run Agent 3 to populate.")
    else:
        st.bar_chart(df.set_index("Role")["Count"])
