"""
People upsert + deduplication.

Match strategy for people:
  1. linkedin_url match (definitive)
  2. (full_name, company_id) trigram match > 0.85
  3. Otherwise → new person

When a duplicate is found we merge: prefer non-null over null, prefer
higher email_status (smtp_verified > mx_valid > pattern_inferred).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agent1_schema.models import Company, Evidence, Person, RawDocument, Source
from agent3_people_discovery.base import PeopleRecord, role_seniority

logger = logging.getLogger(__name__)

EMAIL_STATUS_RANK = {
    None: 0,
    "unverified": 1,
    "pattern_inferred": 2,
    "mx_valid": 3,
    "catch_all": 4,
    "smtp_verified": 5,
    "invalid": -1,
}


def find_existing_person(
    session: Session, record: PeopleRecord, company: Company
) -> Optional[Person]:
    if record.linkedin_url:
        p = session.execute(
            select(Person)
            .where(Person.linkedin_url == record.linkedin_url)
            .where(Person.deleted_at.is_(None))
        ).scalar_one_or_none()
        if p:
            return p

    rows = session.execute(
        text(
            """
            SELECT id FROM people
            WHERE company_id = :cid
              AND deleted_at IS NULL
              AND similarity(lower(full_name), lower(:name)) > 0.85
            ORDER BY similarity(lower(full_name), lower(:name)) DESC
            LIMIT 1
            """
        ),
        {"cid": str(company.id), "name": record.full_name},
    ).first()
    if rows:
        return session.get(Person, rows.id)
    return None


def upsert_person(
    session: Session,
    record: PeopleRecord,
    company: Company,
    raw_doc: Optional[RawDocument] = None,
) -> Person:
    existing = find_existing_person(session, record, company)
    if existing:
        _merge_person(existing, record)
        person = existing
    else:
        person = Person(
            company_id=company.id,
            full_name=record.full_name,
            given_name=record.given_name,
            family_name=record.family_name,
            title=record.title,
            role_category=record.role_category,
            seniority=role_seniority(record.role_category),
            email=record.email,
            email_status=record.email_status,
            email_confidence=record.email_confidence,
            phone=record.phone,
            linkedin_url=record.linkedin_url,
            other_profiles=record.other_profiles,
            purpose="b2b_sales_research:it_decision_maker_outreach",
            legal_basis="public_source",
        )
        session.add(person)
        session.flush()

    person.last_verified = datetime.utcnow()

    if raw_doc:
        session.add(
            Evidence(
                raw_document_id=raw_doc.id,
                entity_type="person",
                entity_id=person.id,
                excerpt=record.excerpt[:500] if record.excerpt else None,
                confidence=record.confidence,
            )
        )

    return person


def _merge_person(p: Person, r: PeopleRecord) -> None:
    # Title: prefer the longer/more-specific
    if r.title and (not p.title or len(r.title) > len(p.title)):
        p.title = r.title
    # Role: only upgrade away from 'other'
    if p.role_category == "other" and r.role_category != "other":
        p.role_category = r.role_category
        p.seniority = role_seniority(r.role_category)
    # LinkedIn URL: fill if missing
    if r.linkedin_url and not p.linkedin_url:
        p.linkedin_url = r.linkedin_url
    # Email: prefer higher confidence/status
    new_rank = EMAIL_STATUS_RANK.get(r.email_status, 0)
    cur_rank = EMAIL_STATUS_RANK.get(p.email_status, 0)
    if r.email and new_rank > cur_rank:
        p.email = r.email
        p.email_status = r.email_status
        p.email_confidence = r.email_confidence
    # Other profiles merge
    if r.other_profiles:
        merged = dict(p.other_profiles or {})
        merged.update(r.other_profiles)
        p.other_profiles = merged
