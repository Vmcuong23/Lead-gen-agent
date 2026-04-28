"""People page — search and filter IT decision-makers across companies."""
from __future__ import annotations

import streamlit as st

from interface.utils import people_df, soft_delete_person

ROLE_CHOICES = [
    "(any)",
    "cio",
    "cdo",
    "cto",
    "head_of_it",
    "it_director",
    "head_of_sap",
    "erp_manager",
    "it_manager",
]
EMAIL_STATUS_CHOICES = [
    "(any)",
    "smtp_verified",
    "mx_valid",
    "catch_all",
    "pattern_inferred",
    "unverified",
    "invalid",
]


def render() -> None:
    st.title("People")
    st.caption("IT decision-makers across all target companies.")

    with st.expander("Filters", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            role = st.selectbox("Role", ROLE_CHOICES)
        with c2:
            country = st.text_input("HQ country (ISO2)", "").upper() or None
        with c3:
            province = st.text_input("Province", "") or None
        c4, c5 = st.columns(2)
        with c4:
            email_status = st.selectbox("Email status", EMAIL_STATUS_CHOICES)
        with c5:
            name_q = st.text_input("Name contains", "")

    df = people_df(
        role=None if role == "(any)" else role,
        country=country,
        province=province,
        email_status=None if email_status == "(any)" else email_status,
        name_query=name_q or None,
    )

    if df.empty:
        st.info("No people match these filters.")
        return

    st.write(f"**{len(df):,} people**")
    display_cols = [c for c in df.columns if c != "id"]

    selection = st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        height=520,
    )

    if not selection or not selection.selection.rows:
        return

    row_idx = selection.selection.rows[0]
    pid = df.iloc[row_idx]["id"]
    name = df.iloc[row_idx]["Name"]
    company = df.iloc[row_idx]["Company"]

    st.divider()
    st.subheader(f"{name} — {df.iloc[row_idx]['Title']}")
    st.caption(company)

    if df.iloc[row_idx]["LinkedIn"]:
        st.markdown(f"🔗 [{df.iloc[row_idx]['LinkedIn']}]({df.iloc[row_idx]['LinkedIn']})")

    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown(
            f"📧 **Email:** {df.iloc[row_idx]['Email'] or '—'} "
            f"*(status: {df.iloc[row_idx]['Email status'] or '—'}, "
            f"confidence: {df.iloc[row_idx]['Email confidence'] or '—'})*"
        )
    with c2:
        with st.popover("⚠️ Erase (PDPL)"):
            st.warning(
                "This soft-deletes the person and clears email/phone/LinkedIn. "
                "Source documents are kept for audit but the row will be hidden "
                "from all queries."
            )
            if st.button("Confirm erasure", type="primary"):
                soft_delete_person(pid)
                st.success("Person erased. Refresh to update the list.")
                st.rerun()
