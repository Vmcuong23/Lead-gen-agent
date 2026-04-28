"""
Entity resolution + upsert for companies and events.

Matching strategy (cheapest to most expensive):
  1. Exact tax_code match → definite same company
  2. Exact normalized legal_name match
  3. Trigram similarity > 0.7 + same vn_province → likely match
  4. (Future) pgvector cosine similarity on name_embedding
  5. Otherwise → new company

When merging, fields fill in (NULL → value) but never overwrite a
trusted value with NULL. Conflicting non-null values create an Evidence
row each so the conflict is preserved for review.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agent1_schema.models import (
    Company,
    Event,
    Evidence,
    RawDocument,
    Source,
)
from agent2_fdi_scraper.base import CompanyRecord, EventRecord

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Lowercase, strip diacritics, collapse legal suffixes for matching."""
    s = unicodedata.normalize("NFD", name)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower()
    # Strip common legal suffixes / prefixes
    suffixes = [
        r"\bco\.?,?\s*ltd\.?\b",
        r"\bcompany\s+limited\b",
        r"\bjoint\s+stock\s+company\b",
        r"\bjsc\b",
        r"\bcorp(?:oration)?\b",
        r"\binc\.?\b",
        r"\bgmbh\b",
        r"\bcty\b",
        r"\bcong ty\b",
        r"\btnhh\b",  # TNHH = trách nhiệm hữu hạn (LLC)
        r"\bmtv\b",   # MTV = một thành viên
    ]
    for s_ in suffixes:
        s = re.sub(s_, " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def get_or_create_source(session: Session, slug: str) -> Source:
    src = session.execute(
        select(Source).where(Source.slug == slug)
    ).scalar_one_or_none()
    if src:
        return src
    src = Source(slug=slug, name=slug, kind="other")
    session.add(src)
    session.flush()
    return src


def store_raw_document(
    session: Session,
    source_slug: str,
    url: str,
    content_hash: str,
    content_type: str,
    storage_path: Optional[str] = None,
) -> RawDocument:
    """Idempotently store a raw doc; returns existing row if hash already seen."""
    src = get_or_create_source(session, source_slug)
    existing = session.execute(
        select(RawDocument)
        .where(RawDocument.source_id == src.id)
        .where(RawDocument.content_hash == content_hash)
    ).scalar_one_or_none()
    if existing:
        return existing

    doc = RawDocument(
        source_id=src.id,
        url=url,
        content_hash=content_hash,
        content_type=content_type,
        storage_path=storage_path,
        parsed=False,
    )
    session.add(doc)
    session.flush()
    return doc


def find_existing_company(
    session: Session, record: CompanyRecord
) -> Optional[Company]:
    # 1. tax_code is the strongest match
    if record.tax_code:
        c = session.execute(
            select(Company).where(Company.tax_code == record.tax_code)
        ).scalar_one_or_none()
        if c:
            return c

    # 2. trigram-similar normalized legal name + province sanity check
    norm = normalize_name(record.legal_name)
    if norm:
        rows = session.execute(
            text(
                """
                SELECT id, legal_name, vn_province
                FROM companies
                WHERE similarity(lower(legal_name), :norm) > 0.7
                ORDER BY similarity(lower(legal_name), :norm) DESC
                LIMIT 5
                """
            ),
            {"norm": norm},
        ).all()
        for row in rows:
            if normalize_name(row.legal_name) == norm:
                if (
                    record.vn_province
                    and row.vn_province
                    and record.vn_province != row.vn_province
                ):
                    continue
                return session.get(Company, row.id)

    # 3. embedding similarity — catches "SSDV" ↔ "Samsung SDI Vietnam"
    #    that trigram misses entirely. Optional: only runs if embeddings
    #    backend is configured.
    try:
        from shared.embeddings import find_similar_companies

        candidates = find_similar_companies(
            record.legal_name, country=record.hq_country, threshold=0.88, limit=3
        )
        for cid, _, sim in candidates:
            company = session.get(Company, cid)
            if company is None:
                continue
            # Province sanity check still applies
            if (
                record.vn_province
                and company.vn_province
                and record.vn_province != company.vn_province
            ):
                continue
            logger.info(
                "Embedding match: %r -> %r (sim=%.3f)",
                record.legal_name,
                company.legal_name,
                sim,
            )
            return company
    except Exception as e:
        logger.debug("Embedding lookup skipped: %s", e)

    return None


def upsert_company(
    session: Session, record: CompanyRecord, doc: RawDocument
) -> Company:
    existing = find_existing_company(session, record)
    if existing:
        _merge_company(existing, record)
        company = existing
    else:
        company = Company(
            legal_name=record.legal_name,
            display_name=record.display_name,
            name_variants=record.name_variants,
            tax_code=record.tax_code,
            erc_number=record.erc_number,
            investment_cert=record.investment_cert,
            hq_country=record.hq_country,
            vn_address=record.vn_address,
            vn_province=record.vn_province,
            industrial_park=record.industrial_park,
            industry_codes=record.industry_codes,
            industry_label=record.industry_label,
            investment_usd=record.investment_usd,
            employee_count=record.employee_count,
            factory_count=record.factory_count,
            first_licensed=record.first_licensed,
            website=record.website,
        )
        # Embed immediately so the *next* dedup query can find this row
        try:
            from shared.embeddings import (
                canonical_name_for_embedding,
                embed_batch,
            )

            [vec] = embed_batch(
                [
                    canonical_name_for_embedding(
                        record.legal_name, country=record.hq_country
                    )
                ]
            )
            company.name_embedding = vec
        except Exception as e:
            logger.debug("Inline embedding skipped: %s", e)

        session.add(company)
        session.flush()

    company.last_enriched = datetime.utcnow()
    _add_evidence(session, doc, "company", company.id, record)
    return company


def _merge_company(existing: Company, new: CompanyRecord) -> None:
    """Fill blanks; never overwrite a trusted value with NULL."""
    fields = [
        "tax_code",
        "erc_number",
        "investment_cert",
        "hq_country",
        "vn_address",
        "vn_province",
        "industrial_park",
        "industry_label",
        "investment_usd",
        "employee_count",
        "factory_count",
        "first_licensed",
        "website",
    ]
    for f in fields:
        new_val = getattr(new, f)
        if new_val is not None and getattr(existing, f) is None:
            setattr(existing, f, new_val)
    # Append new variants
    for v in new.name_variants:
        if v and v not in existing.name_variants:
            existing.name_variants = [*existing.name_variants, v]


def _add_evidence(
    session: Session,
    doc: RawDocument,
    entity_type: str,
    entity_id: uuid.UUID,
    record: CompanyRecord,
) -> None:
    session.add(
        Evidence(
            raw_document_id=doc.id,
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=None,
            excerpt=record.legal_name,
            confidence=record.confidence,
        )
    )


def upsert_event(
    session: Session,
    event: EventRecord,
    company: Company,
    doc: RawDocument,
) -> Event:
    """Insert event; idempotent on (company_id, kind, occurred_on)."""
    if event.occurred_on:
        existing = session.execute(
            select(Event)
            .where(Event.company_id == company.id)
            .where(Event.kind == event.kind)
            .where(Event.occurred_on == event.occurred_on)
        ).scalar_one_or_none()
        if existing:
            return existing

    e = Event(
        kind=event.kind,
        company_id=company.id,
        occurred_on=event.occurred_on,
        summary=event.summary,
        payload=event.payload,
    )
    session.add(e)
    session.flush()
    session.add(
        Evidence(
            raw_document_id=doc.id,
            entity_type="event",
            entity_id=e.id,
            excerpt=event.summary[:500] if event.summary else None,
            confidence=70,
        )
    )
    return e
