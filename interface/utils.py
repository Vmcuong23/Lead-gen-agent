"""Shared helpers for the dashboard pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from agent1_schema.models import (
    Company,
    CompanySignal,
    Evidence,
    Event,
    Person,
    RawDocument,
    Source,
)
from agent1_schema.models.db import session_scope


@st.cache_data(ttl=30)
def companies_df(
    province: str | None = None,
    country: str | None = None,
    industry: str | None = None,
    min_priority: int = 0,
    has_decisionmaker_only: bool = False,
    name_query: str | None = None,
) -> pd.DataFrame:
    with session_scope() as s:
        q = select(Company).where(Company.status == "active")
        if province:
            q = q.where(Company.vn_province == province)
        if country:
            q = q.where(Company.hq_country == country)
        if industry:
            q = q.where(Company.industry_label.ilike(f"%{industry}%"))
        if min_priority:
            q = q.where(Company.target_priority >= min_priority)
        if name_query:
            q = q.where(Company.legal_name.ilike(f"%{name_query}%"))
        q = q.order_by(Company.target_priority.desc(), Company.legal_name)
        rows = s.execute(q).scalars().all()

        if has_decisionmaker_only:
            ids_with_dm = set(
                r[0]
                for r in s.execute(
                    select(Person.company_id)
                    .where(Person.deleted_at.is_(None))
                    .where(
                        Person.role_category.in_(
                            [
                                "cio",
                                "cdo",
                                "cto",
                                "head_of_it",
                                "it_director",
                                "head_of_sap",
                                "erp_manager",
                                "it_manager",
                            ]
                        )
                    )
                    .distinct()
                )
                if r[0]
            )
            rows = [c for c in rows if c.id in ids_with_dm]

        people_count = dict(
            s.execute(
                select(Person.company_id, func.count(Person.id))
                .where(Person.deleted_at.is_(None))
                .group_by(Person.company_id)
            ).all()
        )

    data = [
        {
            "id": str(c.id),
            "Priority": c.target_priority,
            "Display name": c.display_name,
            "Country": c.hq_country,
            "Province": c.vn_province,
            "Industry": c.industry_label,
            "Investment (USD)": c.investment_usd,
            "First licensed": c.first_licensed,
            "Industrial park": c.industrial_park,
            "People found": people_count.get(c.id, 0),
            "Website": c.website,
            "Last enriched": c.last_enriched,
        }
        for c in rows
    ]
    return pd.DataFrame(data)


@st.cache_data(ttl=30)
def people_df(
    role: str | None = None,
    country: str | None = None,
    province: str | None = None,
    email_status: str | None = None,
    name_query: str | None = None,
) -> pd.DataFrame:
    with session_scope() as s:
        q = (
            select(Person, Company)
            .join(Company, Person.company_id == Company.id, isouter=True)
            .where(Person.deleted_at.is_(None))
        )
        if role:
            q = q.where(Person.role_category == role)
        if country:
            q = q.where(Company.hq_country == country)
        if province:
            q = q.where(Company.vn_province == province)
        if email_status:
            q = q.where(Person.email_status == email_status)
        if name_query:
            q = q.where(Person.full_name.ilike(f"%{name_query}%"))
        q = q.order_by(
            Company.target_priority.desc().nulls_last(), Person.full_name
        )
        rows = s.execute(q).all()

    data = [
        {
            "id": str(p.id),
            "Name": p.full_name,
            "Title": p.title,
            "Role": p.role_category,
            "Seniority": p.seniority,
            "Company": (c.display_name if c else None),
            "Country": (c.hq_country if c else None),
            "Province": (c.vn_province if c else None),
            "Email": p.email,
            "Email status": p.email_status,
            "Email confidence": p.email_confidence,
            "LinkedIn": p.linkedin_url,
            "Last verified": p.last_verified,
            "Priority": (c.target_priority if c else 0),
        }
        for p, c in rows
    ]
    return pd.DataFrame(data)


@st.cache_data(ttl=30)
def overview_metrics() -> dict:
    with session_scope() as s:
        return {
            "companies": s.scalar(
                select(func.count(Company.id)).where(Company.status == "active")
            )
            or 0,
            "companies_hot": s.scalar(
                select(func.count(Company.id))
                .where(Company.status == "active")
                .where(Company.target_priority >= 75)
            )
            or 0,
            "people": s.scalar(
                select(func.count(Person.id)).where(Person.deleted_at.is_(None))
            )
            or 0,
            "people_with_email": s.scalar(
                select(func.count(Person.id))
                .where(Person.deleted_at.is_(None))
                .where(Person.email.is_not(None))
            )
            or 0,
            "people_smtp_verified": s.scalar(
                select(func.count(Person.id))
                .where(Person.deleted_at.is_(None))
                .where(Person.email_status == "smtp_verified")
            )
            or 0,
            "events": s.scalar(select(func.count(Event.id))) or 0,
            "raw_docs": s.scalar(select(func.count(RawDocument.id))) or 0,
        }


@st.cache_data(ttl=30)
def role_breakdown() -> pd.DataFrame:
    with session_scope() as s:
        rows = s.execute(
            select(Person.role_category, func.count(Person.id))
            .where(Person.deleted_at.is_(None))
            .group_by(Person.role_category)
            .order_by(func.count(Person.id).desc())
        ).all()
    return pd.DataFrame(rows, columns=["Role", "Count"])


def get_company(company_id: str):
    with session_scope() as s:
        return s.get(Company, company_id)


def get_company_events(company_id: str) -> list:
    with session_scope() as s:
        return list(
            s.execute(
                select(Event)
                .where(Event.company_id == company_id)
                .order_by(Event.occurred_on.desc().nulls_last())
            ).scalars()
        )


def get_company_people(company_id: str) -> list:
    with session_scope() as s:
        return list(
            s.execute(
                select(Person)
                .where(Person.company_id == company_id)
                .where(Person.deleted_at.is_(None))
                .order_by(Person.role_category)
            ).scalars()
        )


def get_company_evidence(company_id: str) -> list:
    with session_scope() as s:
        return list(
            s.execute(
                select(Evidence, RawDocument, Source)
                .join(RawDocument, Evidence.raw_document_id == RawDocument.id)
                .join(Source, RawDocument.source_id == Source.id)
                .where(Evidence.entity_type == "company")
                .where(Evidence.entity_id == company_id)
                .order_by(Evidence.extracted_at.desc())
            ).all()
        )


def soft_delete_person(person_id: str) -> None:
    """PDPL-required erasure path."""
    from datetime import datetime

    with session_scope() as s:
        p = s.get(Person, person_id)
        if p:
            p.deleted_at = datetime.utcnow()
            p.email = None
            p.phone = None
            p.linkedin_url = None
            p.other_profiles = {}
    st.cache_data.clear()
