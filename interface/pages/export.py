"""Export page — download filtered companies or people as CSV."""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from interface.utils import companies_df, people_df


def render() -> None:
    st.title("Export")
    st.caption("Download filtered slices for outreach campaigns or analysis.")

    tab_co, tab_p = st.tabs(["Companies", "People"])

    with tab_co:
        c1, c2, c3 = st.columns(3)
        with c1:
            province = st.text_input("Province", "", key="exp_co_prov") or None
        with c2:
            country = st.text_input("HQ country", "", key="exp_co_co").upper() or None
        with c3:
            min_priority = st.slider("Min priority", 0, 100, 50, key="exp_co_pri")
        df = companies_df(
            province=province, country=country, min_priority=min_priority
        )
        st.write(f"{len(df):,} rows")
        st.dataframe(df.drop(columns=["id"]), height=300, hide_index=True)
        st.download_button(
            "📥 Download companies CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name=f"companies_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )

    with tab_p:
        c1, c2, c3 = st.columns(3)
        with c1:
            role = st.text_input("Role category (e.g. cio)", "", key="exp_p_role") or None
        with c2:
            country = st.text_input("HQ country", "", key="exp_p_co").upper() or None
        with c3:
            email_status = st.selectbox(
                "Email status",
                ["(any)", "smtp_verified", "mx_valid", "catch_all", "pattern_inferred"],
                key="exp_p_es",
            )
        df = people_df(
            role=role,
            country=country,
            email_status=None if email_status == "(any)" else email_status,
        )
        st.write(f"{len(df):,} rows")
        st.dataframe(df.drop(columns=["id"]), height=300, hide_index=True)
        st.download_button(
            "📥 Download people CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name=f"people_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )

    st.divider()
    st.warning(
        "PDPL reminder: any export of personal data must align with the "
        "purpose declared in the people record (`b2b_sales_research:"
        "it_decision_maker_outreach`). Don't share these CSVs externally "
        "without a data-processing agreement in place."
    )
