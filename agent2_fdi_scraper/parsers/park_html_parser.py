"""
Industrial-park tenant-list parser.

These pages typically contain a table or grid of tenant logos and names,
sometimes with country and industry hints. We strip irrelevant chrome
(nav/footer) and feed the cleaned HTML to Claude.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import anthropic
from pydantic import BaseModel, Field, ValidationError
from selectolax.parser import HTMLParser

from agent2_fdi_scraper.base import CompanyRecord, ParseResult, Parser

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

PROMPT = """\
Extract the list of tenant companies from this Vietnamese industrial park \
page. For each tenant, return the company name, its origin country (ISO2 \
if you can infer from the name/logo description, else null), and industry \
if mentioned.

Output ONLY this JSON shape (no preamble, no fences):

{
  "park_name": "string (e.g. 'VSIP Bắc Ninh')",
  "tenants": [
    {
      "legal_name": "string",
      "display_name": "string (short form)",
      "hq_country": "ISO2 or null",
      "industry_label": "string or null"
    }
  ]
}

Skip generic items (logos with no readable name, decorative content). \
Only include companies that look like real corporate tenants.
"""


class _Tenant(BaseModel):
    legal_name: str
    display_name: str
    hq_country: Optional[str] = None
    industry_label: Optional[str] = None


class _ParkOut(BaseModel):
    park_name: str
    tenants: list[_Tenant] = Field(default_factory=list)


class IndustrialParkHTMLParser(Parser):
    def __init__(self, client: anthropic.Anthropic | None = None):
        self._client = client or anthropic.Anthropic()

    def parse(self, content: bytes, content_type: str, url: str) -> ParseResult:
        cleaned = self._clean_html(content)
        if len(cleaned) < 200:
            logger.warning("Cleaned HTML for %s is suspiciously short", url)
            return ParseResult()

        msg = self._client.messages.create(
            model=MODEL,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{PROMPT}\n\n--- BEGIN PAGE ({url}) ---\n{cleaned}\n--- END PAGE ---"
                    ),
                }
            ],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]

        try:
            extracted = _ParkOut.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error("Park parser failed for %s: %s", url, e)
            return ParseResult()

        companies = [
            CompanyRecord(
                legal_name=t.legal_name,
                display_name=t.display_name,
                hq_country=t.hq_country,
                industry_label=t.industry_label,
                industrial_park=extracted.park_name,
                confidence=60,  # park lists alone are weaker evidence
            )
            for t in extracted.tenants
        ]
        return ParseResult(companies=companies)

    @staticmethod
    def _clean_html(content: bytes) -> str:
        """Strip nav/footer/scripts; keep main content."""
        try:
            tree = HTMLParser(content.decode("utf-8", errors="replace"))
        except Exception:
            return ""
        for sel in ("script", "style", "nav", "footer", "header", "aside"):
            for node in tree.css(sel):
                node.decompose()
        # Prefer <main> or <article> if present, else body
        main = tree.css_first("main") or tree.css_first("article") or tree.body
        if main is None:
            return ""
        text = main.text(separator="\n", strip=True)
        # Truncate aggressively — tenant lists are short
        return text[:30_000]
