"""
SQLAlchemy ORM models for the FDI Agent shared database.

Importable from any agent: `from agent1_schema.models import Company, Person`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Sources / raw documents
# ---------------------------------------------------------------------------
class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RawDocument(Base):
    __tablename__ = "raw_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(Text)
    storage_path: Mapped[Optional[str]] = mapped_column(Text)
    parsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parse_error: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_raw_doc_source_hash"),
    )


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------
class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_variants: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    name_embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1024))

    tax_code: Mapped[Optional[str]] = mapped_column(Text, unique=True)
    erc_number: Mapped[Optional[str]] = mapped_column(Text)
    investment_cert: Mapped[Optional[str]] = mapped_column(Text)

    hq_country: Mapped[Optional[str]] = mapped_column(Text)
    vn_address: Mapped[Optional[str]] = mapped_column(Text)
    vn_province: Mapped[Optional[str]] = mapped_column(Text)
    vn_lat: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    vn_lng: Mapped[Optional[float]] = mapped_column(Numeric(9, 6))
    industrial_park: Mapped[Optional[str]] = mapped_column(Text)

    industry_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    industry_label: Mapped[Optional[str]] = mapped_column(Text)
    investment_usd: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    employee_count: Mapped[Optional[int]] = mapped_column(Integer)
    factory_count: Mapped[Optional[int]] = mapped_column(Integer)
    first_licensed: Mapped[Optional[date]] = mapped_column(Date)

    website: Mapped[Optional[str]] = mapped_column(Text)
    domains: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    linkedin_url: Mapped[Optional[str]] = mapped_column(Text)
    facebook_url: Mapped[Optional[str]] = mapped_column(Text)
    tech_stack: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    target_priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_enriched: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    people: Mapped[list["Person"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','dormant','closed')", name="ck_companies_status"
        ),
    )


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
class Person(Base):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL")
    )

    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    given_name: Mapped[Optional[str]] = mapped_column(Text)
    family_name: Mapped[Optional[str]] = mapped_column(Text)
    name_embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1024))

    title: Mapped[str] = mapped_column(Text, nullable=False)
    role_category: Mapped[str] = mapped_column(Text, nullable=False)
    seniority: Mapped[Optional[str]] = mapped_column(Text)
    reports_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )

    email: Mapped[Optional[str]] = mapped_column(Text)
    email_status: Mapped[Optional[str]] = mapped_column(Text)
    email_confidence: Mapped[Optional[int]] = mapped_column(SmallInteger)
    phone: Mapped[Optional[str]] = mapped_column(Text)

    linkedin_url: Mapped[Optional[str]] = mapped_column(Text, unique=True)
    other_profiles: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    purpose: Mapped[str] = mapped_column(
        Text, nullable=False, default="b2b_sales_research:it_decision_maker_outreach"
    )
    legal_basis: Mapped[str] = mapped_column(Text, nullable=False, default="public_source")
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_verified: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    company: Mapped[Optional[Company]] = relationship(back_populates="people")

    __table_args__ = (
        CheckConstraint(
            "role_category IN ('cio','cdo','cto','head_of_it','it_director',"
            "'head_of_sap','erp_manager','it_manager','other')",
            name="ck_people_role_category",
        ),
        CheckConstraint(
            "seniority IS NULL OR seniority IN "
            "('c_level','vp','director','manager','individual')",
            name="ck_people_seniority",
        ),
        CheckConstraint(
            "email_status IS NULL OR email_status IN "
            "('unverified','pattern_inferred','mx_valid','smtp_verified',"
            "'invalid','catch_all')",
            name="ck_people_email_status",
        ),
    )


# ---------------------------------------------------------------------------
# Events / evidence / signals / jobs
# ---------------------------------------------------------------------------
class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    company_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE")
    )
    person_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id", ondelete="CASCADE")
    )
    occurred_on: Mapped[Optional[date]] = mapped_column(Date)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[Optional[str]] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_documents.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_name: Mapped[Optional[str]] = mapped_column(Text)
    excerpt: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CompanySignal(Base):
    __tablename__ = "company_signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    error: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
