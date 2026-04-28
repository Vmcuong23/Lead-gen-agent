"""
Agent 3 — People Discovery
==========================

For each target company in the DB, find IT decision-makers
(CIO/CDO/CTO/Head of IT/IT Director/Head of SAP/ERP Manager/IT Manager)
using only public, defensible sources.

Sources (in order of yield/quality):
  1. SERP queries against Google/Bing/Brave for "site:linkedin.com/in" with
     role + company filters. We extract name + title + LI URL from
     search-results pages — never from LinkedIn itself.
  2. Company `/about`, `/leadership`, `/team` pages.
  3. Press release archives (mentions like "Mr. X, IT Director of Y").
  4. Conference speaker pages (Vietnam CIO Summit, SAP NOW Vietnam, etc.).

Each source emits PeopleRecord objects, which the orchestrator dedupes
and upserts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional

from agent1_schema.models import Company

ROLE_CATEGORIES = {
    "cio": ["CIO", "Chief Information Officer"],
    "cdo": ["CDO", "Chief Digital Officer", "Chief Data Officer"],
    "cto": ["CTO", "Chief Technology Officer", "Chief Technical Officer"],
    "head_of_it": ["Head of IT", "Head of Information Technology"],
    "it_director": ["IT Director", "Director of IT", "Director, IT"],
    "head_of_sap": ["Head of SAP", "SAP Lead", "SAP Director"],
    "erp_manager": ["ERP Manager", "Manager, ERP"],
    "it_manager": ["IT Manager", "Manager, IT", "Information Technology Manager"],
}


def normalize_role(title: str) -> str:
    """Map a free-text title to one of the role_category enum values."""
    t = title.lower()
    # Order matters — check more specific first
    if "chief information officer" in t or t.strip() == "cio":
        return "cio"
    if "chief digital officer" in t or "chief data officer" in t:
        return "cdo"
    if "chief technology officer" in t or "chief technical officer" in t:
        return "cto"
    if "head of sap" in t or "sap lead" in t or "sap director" in t:
        return "head_of_sap"
    if "head of it" in t or "head of information technology" in t:
        return "head_of_it"
    if "it director" in t or "director of it" in t or "director, it" in t:
        return "it_director"
    if "erp" in t and ("manager" in t or "lead" in t):
        return "erp_manager"
    if "it manager" in t or "information technology manager" in t:
        return "it_manager"
    return "other"


def role_seniority(category: str) -> str:
    if category in {"cio", "cdo", "cto"}:
        return "c_level"
    if category in {"head_of_it", "it_director", "head_of_sap"}:
        return "director"
    if category in {"erp_manager", "it_manager"}:
        return "manager"
    return "individual"


@dataclass
class PeopleRecord:
    """One person discovered for one company."""

    full_name: str
    title: str
    role_category: str  # already normalized
    company_id: Optional[str] = None  # UUID as string
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    linkedin_url: Optional[str] = None
    other_profiles: dict[str, str] = field(default_factory=dict)
    email: Optional[str] = None
    email_status: Optional[str] = None
    email_confidence: Optional[int] = None
    phone: Optional[str] = None
    source_slug: str = ""
    source_url: str = ""
    excerpt: str = ""
    confidence: int = 60


class DiscoverySource(ABC):
    """A source of PeopleRecord objects scoped to a single company."""

    slug: str

    @abstractmethod
    def find_people(self, company: Company) -> Iterator[PeopleRecord]:
        """Yield PeopleRecord objects discovered for this company."""
        ...
