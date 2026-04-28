"""
Company-website people discovery.

Many FDI companies in Vietnam publish leadership/management pages on
their .com.vn or .com sites. This source:
  1. Probes a list of common URL paths (/about, /leadership, /our-team,
     /management, /lanh-dao, /ve-chung-toi, ...).
  2. For each page that returns 200 and has substantive content,
     hands the cleaned text to Claude with an extraction prompt.
  3. Filters extracted people to IT-related roles only.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Iterator, Optional
from urllib.parse import urljoin, urlparse

import anthropic
import httpx
from pydantic import BaseModel, Field, ValidationError
from selectolax.parser import HTMLParser

from agent1_schema.models import Company
from agent3_people_discovery.base import (
    DiscoverySource,
    PeopleRecord,
    normalize_role,
)

logger = logging.getLogger(__name__)

PROBE_PATHS = [
    # English
    "/about",
    "/about-us",
    "/leadership",
    "/management",
    "/team",
    "/our-team",
    "/our-people",
    "/executives",
    # Vietnamese
    "/ve-chung-toi",
    "/lanh-dao",
    "/ban-lanh-dao",
    "/doi-ngu",
    "/nhan-su",
]

EXTRACTION_PROMPT = """\
Extract every named person on this corporate "About / Leadership / Team" page. \
Return a JSON object (no markdown fences) of the form:

{
  "people": [
    {
      "full_name": "string",
      "title": "string (their job title as written)",
      "email": "string or null (only if explicitly published)"
    }
  ]
}

Rules:
- Only people who are explicitly identified by name AND title.
- Do NOT invent emails. Only include emails that appear verbatim on the page.
- Preserve the original title text (don't translate "Giám đốc" — leave it).
- Skip generic placeholders like "Our Team", "Founder & CEO" without a name.
"""

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")


class _ExtractedPerson(BaseModel):
    full_name: str
    title: str
    email: Optional[str] = None


class _ExtractedOut(BaseModel):
    people: list[_ExtractedPerson] = Field(default_factory=list)


class CompanyWebsiteSource(DiscoverySource):
    slug = "company_website"

    def __init__(
        self,
        http: httpx.Client | None = None,
        client: anthropic.Anthropic | None = None,
    ):
        self._http = http or httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FDIAgent/0.1)"},
        )
        self._claude = client or anthropic.Anthropic()

    def find_people(self, company: Company) -> Iterator[PeopleRecord]:
        if not company.website:
            return
        for url in self._candidate_urls(company.website):
            html = self._safe_get(url)
            if not html:
                continue
            text = self._clean(html)
            if len(text) < 200:
                continue
            try:
                people = self._extract(text)
            except Exception as e:
                logger.warning("Extract failed for %s: %s", url, e)
                continue
            for p in people:
                category = normalize_role(p.title)
                if category == "other":
                    continue
                yield PeopleRecord(
                    full_name=p.full_name,
                    title=p.title,
                    role_category=category,
                    email=p.email,
                    email_status="unverified" if p.email else None,
                    email_confidence=85 if p.email else None,
                    source_slug=self.slug,
                    source_url=url,
                    excerpt=f"From {url}",
                    confidence=85,
                )

    def _candidate_urls(self, website: str) -> list[str]:
        # Normalize base
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
        parsed = urlparse(website)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return [urljoin(base, p) for p in PROBE_PATHS]

    def _safe_get(self, url: str) -> Optional[str]:
        try:
            r = self._http.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or "text/html" not in r.headers.get(
            "content-type", ""
        ):
            return None
        return r.text

    @staticmethod
    def _clean(html: str) -> str:
        tree = HTMLParser(html)
        for sel in ("script", "style", "nav", "footer", "aside"):
            for n in tree.css(sel):
                n.decompose()
        body = tree.body
        if body is None:
            return ""
        return body.text(separator="\n", strip=True)[:20_000]

    def _extract(self, text: str) -> list[_ExtractedPerson]:
        msg = self._claude.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{EXTRACTION_PROMPT}\n\n--- BEGIN PAGE ---\n{text}\n--- END PAGE ---"
                    ),
                }
            ],
        )
        out = "".join(b.text for b in msg.content if b.type == "text").strip()
        if out.startswith("```"):
            out = out.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        if out.lstrip().startswith("json"):
            out = out.lstrip()[4:]
        try:
            return _ExtractedOut.model_validate(json.loads(out)).people
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("People-extract validation failed: %s", e)
            return []
