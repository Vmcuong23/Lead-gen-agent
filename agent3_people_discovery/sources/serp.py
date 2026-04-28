"""
SERP-based people discovery.

We query a search engine API (Brave Search by default; SerpAPI/Tavily as
alternatives) for things like:

    site:linkedin.com/in "IT Director" "Samsung Vietnam"
    site:linkedin.com/in "Head of SAP" "Samsung SDI"

The JSON response contains:
  - title (usually "Person Name - Title - Company | LinkedIn")
  - url (https://www.linkedin.com/in/<slug>)
  - description (snippet from the profile)

We parse the title with a deterministic regex first (it's structured),
fall back to Claude for ambiguous cases, and emit one PeopleRecord per
hit.

CRITICAL: this module never fetches linkedin.com. Search results are
indexed publicly by search engines; we only consume the API response.
That keeps us out of LinkedIn ToS territory and out of PDPL trouble
(the data subject placed it on LinkedIn for public discovery, search
engines indexed it for public discovery).
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Iterator, Optional
from urllib.parse import urlparse

import httpx

from agent1_schema.models import Company
from agent3_people_discovery.base import (
    ROLE_CATEGORIES,
    DiscoverySource,
    PeopleRecord,
    normalize_role,
)

logger = logging.getLogger(__name__)

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
LI_PROFILE_URL = re.compile(r"https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/?#]+")
# "Tran Van A - IT Director - Samsung Vietnam | LinkedIn"
TITLE_PATTERN = re.compile(
    r"^(?P<name>[^-|]+?)\s*[-–]\s*(?P<title>[^-|]+?)\s*[-–]\s*(?P<company>[^|]+?)\s*\|\s*LinkedIn",
    re.IGNORECASE,
)


class BraveSearchClient:
    """Thin wrapper around Brave Search API."""

    def __init__(self, api_key: Optional[str] = None, http: httpx.Client | None = None):
        self.api_key = api_key or os.environ.get("BRAVE_SEARCH_API_KEY")
        if not self.api_key:
            raise RuntimeError("BRAVE_SEARCH_API_KEY not set")
        self._http = http or httpx.Client(timeout=20.0)

    def search(self, query: str, count: int = 20) -> list[dict]:
        resp = self._http.get(
            BRAVE_ENDPOINT,
            params={"q": query, "count": count},
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
        )
        if resp.status_code == 429:
            logger.warning("Brave rate limited, sleeping 2s")
            time.sleep(2)
            return self.search(query, count)
        resp.raise_for_status()
        web = resp.json().get("web", {}) or {}
        return web.get("results", []) or []


class SERPDiscoverySource(DiscoverySource):
    slug = "brave_search"

    def __init__(
        self,
        client: BraveSearchClient | None = None,
        roles: Optional[list[str]] = None,
        max_per_role: int = 10,
    ):
        self.client = client or BraveSearchClient()
        # Use all roles by default
        self.roles = roles or [
            phrase
            for phrases in ROLE_CATEGORIES.values()
            for phrase in phrases
        ]
        self.max_per_role = max_per_role

    def find_people(self, company: Company) -> Iterator[PeopleRecord]:
        company_terms = self._company_search_terms(company)
        seen_urls: set[str] = set()

        for role in self.roles:
            for company_term in company_terms:
                query = f'site:linkedin.com/in "{role}" "{company_term}"'
                logger.info("SERP query: %s", query)
                try:
                    results = self.client.search(query, count=self.max_per_role)
                except httpx.HTTPError as e:
                    logger.warning("SERP query failed (%s): %s", query, e)
                    continue

                for r in results:
                    record = self._result_to_record(r, company)
                    if record is None:
                        continue
                    if record.linkedin_url and record.linkedin_url in seen_urls:
                        continue
                    if record.linkedin_url:
                        seen_urls.add(record.linkedin_url)
                    yield record

    @staticmethod
    def _company_search_terms(company: Company) -> list[str]:
        terms: list[str] = [company.display_name]
        if company.legal_name and company.legal_name != company.display_name:
            terms.append(company.legal_name)
        for v in company.name_variants[:2]:
            if v and v not in terms:
                terms.append(v)
        return terms

    @staticmethod
    def _result_to_record(result: dict, company: Company) -> Optional[PeopleRecord]:
        url = (result.get("url") or "").strip()
        title = (result.get("title") or "").strip()
        snippet = (result.get("description") or "").strip()
        if not url or not LI_PROFILE_URL.match(url):
            return None

        # Normalize URL — drop query/fragment
        parsed = urlparse(url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"

        m = TITLE_PATTERN.match(title)
        if not m:
            # Fall back to splitting heuristics; title sometimes uses different delimiters
            return SERPDiscoverySource._fallback_parse(
                clean_url, title, snippet, company
            )

        name = m.group("name").strip()
        role_title = m.group("title").strip()
        result_company = m.group("company").strip()

        if not _company_matches(result_company, company):
            return None

        category = normalize_role(role_title)
        if category == "other":
            return None

        return PeopleRecord(
            full_name=name,
            title=role_title,
            role_category=category,
            linkedin_url=clean_url,
            source_slug="brave_search",
            source_url=clean_url,
            excerpt=f"{title} :: {snippet}"[:500],
            confidence=80,
        )

    @staticmethod
    def _fallback_parse(
        url: str, title: str, snippet: str, company: Company
    ) -> Optional[PeopleRecord]:
        # Less reliable; mark with lower confidence
        # Try splitting on " | " or " - " loosely
        head = title.split("|")[0]
        parts = re.split(r"\s+[-–]\s+", head)
        if len(parts) < 2:
            return None
        name = parts[0].strip()
        role_title = parts[1].strip()
        category = normalize_role(role_title)
        if category == "other":
            return None
        # Verify company is mentioned somewhere
        if not _company_matches(title + " " + snippet, company):
            return None
        return PeopleRecord(
            full_name=name,
            title=role_title,
            role_category=category,
            linkedin_url=url,
            source_slug="brave_search",
            source_url=url,
            excerpt=f"{title} :: {snippet}"[:500],
            confidence=55,
        )


def _company_matches(haystack: str, company: Company) -> bool:
    h = haystack.lower()
    candidates = [company.display_name.lower(), company.legal_name.lower()] + [
        v.lower() for v in (company.name_variants or [])
    ]
    # Require any 2+ words from the company display name to appear
    for cand in candidates:
        words = [w for w in re.split(r"\W+", cand) if len(w) > 2]
        if not words:
            continue
        if sum(1 for w in words if w in h) >= max(1, min(2, len(words))):
            return True
    return False
