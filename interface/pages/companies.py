"""Companies page — list view, filters, and detail drawer."""
from __future__ import annotations

import streamlit as st

from interface.utils import (
    companies_df,
    get_company,
    get_company_evidence,
    get_company_events,
    get_company_people,
)


def render() -> None:
    st.title("Companies")
    st.caption("Filter your target list. Click a company to see details.")

    with st.expander("Filters", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            province = st.text_input("Province (exact)", "")
        with c2:
            country = st.text_input("HQ country (ISO2)", "").upper() or None
        with c3:
            industry = st.text_input("Industry contains", "")
        with c4:
            min_priority = st.slider("Min priority", 0, 100, 0)
        c5, c6 = st.columns(2)
        with c5:
            name_q = st.text_input("Name contains", "")
        with c6:
            dm_only = st.checkbox("Only with IT decision-makers found")

    df = companies_df(
        province=province or None,
        country=country,
        industry=industry or None,
        min_priority=min_priority,
        has_decisionmaker_only=dm_only,
        name_query=name_q or None,
    )

    if df.empty:
        st.info("No companies match these filters.")
        return

    st.write(f"**{len(df):,} companies**")

    # Hide the id column from the user-facing view but keep it in df
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
        st.info("← select a row to see details")
        return

    row_idx = selection.selection.rows[0]
    company_id = df.iloc[row_idx]["id"]
    _render_detail(company_id)


def _render_detail(company_id: str) -> None:
    company = get_company(company_id)
    if not company:
        st.error("Company not found.")
        return

    st.divider()
    st.subheader(company.display_name)
    st.caption(company.legal_name)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Priority", company.target_priority)
    c2.metric("Country", company.hq_country or "—")
    c3.metric(
        "Investment",
        f"${company.investment_usd:,.0f}" if company.investment_usd else "—",
    )
    c4.metric(
        "Licensed",
        company.first_licensed.isoformat() if company.first_licensed else "—",
    )

    if company.website:
        st.markdown(f"🌐 **Website:** [{company.website}]({company.website})")
    if company.industrial_park:
        st.markdown(f"🏭 **Industrial park:** {company.industrial_park}")
    if company.vn_address:
        st.markdown(f"📍 **Address:** {company.vn_address}")

    tab_people, tab_events, tab_evidence = st.tabs(
        ["People", "Events", "Evidence"]
    )

    with tab_people:
        people = get_company_people(company_id)
        if not people:
            st.info("No people discovered yet for this company.")
        else:
            st.dataframe(
                [
                    {
                        "Name": p.full_name,
                        "Title": p.title,
                        "Role": p.role_category,
                        "Email": p.email or "—",
                        "Email status": p.email_status or "—",
                        "LinkedIn": p.linkedin_url or "—",
                        "Verified": p.last_verified,
                    }
                    for p in people
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tab_events:
        events = get_company_events(company_id)
        if not events:
            st.info("No events recorded.")
        else:
            for e in events:
                date_str = e.occurred_on.isoformat() if e.occurred_on else "—"
                st.markdown(f"**{date_str}** · *{e.kind}* — {e.summary or '(no summary)'}")
                if e.payload:
                    with st.expander("Payload"):
                        st.json(e.payload)

    with tab_evidence:
        ev = get_company_evidence(company_id)
        if not ev:
            st.info("No evidence rows.")
        else:
            for evidence, doc, src in ev:
                st.markdown(
                    f"**{src.name}** ({src.kind}) · "
                    f"confidence {evidence.confidence} · "
                    f"[doc]({doc.url})"
                )
                if evidence.excerpt:
                    st.caption(evidence.excerpt[:300])
