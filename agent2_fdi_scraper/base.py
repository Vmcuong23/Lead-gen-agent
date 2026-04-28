"""
Agent 2 — FDI Scraper
=====================

Discovers and enriches FDI companies operating in Vietnam.

Each source plugin implements two methods:

    discover() -> Iterator[RawDocument]
        Fetch new documents from the source, dedupe, store raw blob,
        return RawDocument rows ready for parsing.

    parse(doc: RawDocument) -> ParseResult
        Extract structured company/event records from one document.

The orchestrator runs `discover()` to enqueue parse jobs, then runs
`parse()` for each unparsed doc and upserts results into the DB.

This separation lets us re-parse all old documents with an improved
prompt without re-fetching.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator, Optional


@dataclass
class CompanyRecord:
    """Normalized company record extracted from any source."""

    legal_name: str
    display_name: str
    name_variants: list[str] = field(default_factory=list)
    tax_code: Optional[str] = None
    erc_number: Optional[str] = None
    investment_cert: Optional[str] = None
    hq_country: Optional[str] = None
    vn_address: Optional[str] = None
    vn_province: Optional[str] = None
    industrial_park: Optional[str] = None
    industry_codes: list[str] = field(default_factory=list)
    industry_label: Optional[str] = None
    investment_usd: Optional[float] = None
    employee_count: Optional[int] = None
    factory_count: Optional[int] = None
    first_licensed: Optional[date] = None
    website: Optional[str] = None
    confidence: int = 70  # 0-100


@dataclass
class EventRecord:
    """Time-stamped fact about a company."""

    kind: str  # 'investment_announced'|'factory_groundbreak'|'license_granted'|...
    occurred_on: Optional[date]
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    # Linked to a company by display_name or tax_code; resolution happens
    # at upsert time
    company_legal_name: Optional[str] = None
    company_tax_code: Optional[str] = None


@dataclass
class ParseResult:
    """What a parser returns for one raw document."""

    companies: list[CompanyRecord] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)
    excerpts: dict[str, str] = field(default_factory=dict)  # field -> source quote


@dataclass
class FetchedDoc:
    """A document just pulled from the network, before DB insertion."""

    url: str
    content_type: str
    content: bytes
    source_slug: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class Source(ABC):
    """Base class for FDI data sources."""

    slug: str  # must match a row in the `sources` table

    @abstractmethod
    def discover(self) -> Iterator[FetchedDoc]:
        """Fetch new documents from this source."""
        ...


class Parser(ABC):
    """Base class for parsers that extract structured data from raw docs."""

    @abstractmethod
    def parse(self, content: bytes, content_type: str, url: str) -> ParseResult:
        """Parse a document into structured records."""
        ...
