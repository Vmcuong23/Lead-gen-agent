"""
FIA bulletin parser — uses Claude API to extract company + event records
from monthly FDI report PDFs.

Strategy: send the PDF directly as a document block to Claude (no OCR
needed — Claude handles PDFs natively up to ~100 pages), with a strict
prompt and JSON schema. Validate with Pydantic before returning.

Why Claude vs regex/heuristics:
  - FIA reports mix narrative paragraphs ("In Q3 2025, foreign investors
    registered ...") with tabular project lists. The format shifts
    between months.
  - Names appear in Vietnamese with diacritics, often with multiple
    rendering variants ("Cty TNHH" vs "Công ty Trách nhiệm hữu hạn").
  - Investment amounts are in mixed units (USD thousand, VND billion).
    LLMs handle the unit conversion in the prompt reliably.

Cost note: each FIA monthly bulletin ~30-80 pages → roughly $0.30-$0.80
per parse with Sonnet. Budget accordingly; cache aggressively.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import date, datetime
from typing import Optional

import anthropic
from pydantic import BaseModel, Field, ValidationError

from agent2_fdi_scraper.base import CompanyRecord, EventRecord, ParseResult, Parser

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = 8000

EXTRACTION_PROMPT = """\
You are extracting FDI (foreign direct investment) records from a Vietnamese \
government bulletin published by the Foreign Investment Agency (Cục Đầu tư \
nước ngoài, MPI).

Extract every newly-licensed foreign-invested project mentioned in this \
document. For each project, extract one company record and one or more \
events (typically 'license_granted' for new projects, 'investment_increase' \
for capital adjustments).

RULES:
1. Only extract foreign-invested companies (vốn FDI / 100% vốn nước ngoài / \
liên doanh). Skip purely domestic Vietnamese companies.
2. Only extract companies with manufacturing/factory operations (sản xuất, \
nhà máy, chế biến, lắp ráp). Skip pure trading/holding companies.
3. Only extract events from the LAST FIVE YEARS (since 2021).
4. Convert all investment amounts to USD. The bulletin may use:
   - "triệu USD" = million USD (multiply by 1,000,000)
   - "tỷ đồng" = billion VND (divide by ~24,000 to get USD)
   - "nghìn USD" = thousand USD (multiply by 1,000)
5. For `hq_country`, use ISO 3166-1 alpha-2 codes (KR, JP, SG, DE, US, TW, CN, HK).
6. For `vn_province`, use the standard Vietnamese province name without \
   prefix ("Bắc Ninh", not "Tỉnh Bắc Ninh"; "Hồ Chí Minh", not "TP. HCM").
7. If a field is not stated in the document, return null. Do NOT guess.
8. The `confidence` field reflects how clearly the document states the \
   record (90+ for explicit table rows, 60-80 for prose-extracted, <60 if \
   you had to infer multiple things).

Output JSON ONLY (no preamble, no markdown fences) matching this schema:

{
  "companies": [
    {
      "legal_name": "string (full registered name)",
      "display_name": "string (short)",
      "name_variants": ["string", ...],
      "tax_code": "string or null",
      "hq_country": "ISO2 or null",
      "vn_address": "string or null",
      "vn_province": "string or null",
      "industrial_park": "string or null",
      "industry_label": "string or null",
      "investment_usd": number or null,
      "first_licensed": "YYYY-MM-DD or null",
      "confidence": 0-100
    }
  ],
  "events": [
    {
      "kind": "license_granted | investment_increase | factory_groundbreak | \
factory_opening",
      "occurred_on": "YYYY-MM-DD or null",
      "summary": "one-line English summary",
      "company_legal_name": "must match a legal_name from companies list",
      "payload": {"investment_usd": number, "any_other_detail": "..."}
    }
  ]
}
"""


class _CompanyOut(BaseModel):
    legal_name: str
    display_name: str
    name_variants: list[str] = Field(default_factory=list)
    tax_code: Optional[str] = None
    hq_country: Optional[str] = None
    vn_address: Optional[str] = None
    vn_province: Optional[str] = None
    industrial_park: Optional[str] = None
    industry_label: Optional[str] = None
    investment_usd: Optional[float] = None
    first_licensed: Optional[date] = None
    confidence: int = 70


class _EventOut(BaseModel):
    kind: str
    occurred_on: Optional[date] = None
    summary: str
    company_legal_name: str
    payload: dict = Field(default_factory=dict)


class _ExtractionOut(BaseModel):
    companies: list[_CompanyOut] = Field(default_factory=list)
    events: list[_EventOut] = Field(default_factory=list)


class FIAPDFParser(Parser):
    """Extract FDI records from FIA bulletin PDFs via Claude."""

    def __init__(self, client: anthropic.Anthropic | None = None):
        self._client = client or anthropic.Anthropic()

    def parse(self, content: bytes, content_type: str, url: str) -> ParseResult:
        if content_type != "application/pdf":
            raise ValueError(f"FIAPDFParser expects PDF, got {content_type}")

        b64 = base64.standard_b64encode(content).decode("ascii")

        msg = self._client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
        )

        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        # Tolerate the rare case where Claude wraps in fences despite instructions
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]

        try:
            data = json.loads(text)
            extracted = _ExtractionOut.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error("FIA parser failed to validate output for %s: %s", url, e)
            logger.debug("Raw model output: %s", text[:2000])
            return ParseResult()

        companies = [
            CompanyRecord(
                legal_name=c.legal_name,
                display_name=c.display_name,
                name_variants=c.name_variants,
                tax_code=c.tax_code,
                hq_country=c.hq_country,
                vn_address=c.vn_address,
                vn_province=c.vn_province,
                industrial_park=c.industrial_park,
                industry_label=c.industry_label,
                investment_usd=c.investment_usd,
                first_licensed=c.first_licensed,
                confidence=c.confidence,
            )
            for c in extracted.companies
        ]
        events = [
            EventRecord(
                kind=e.kind,
                occurred_on=e.occurred_on,
                summary=e.summary,
                company_legal_name=e.company_legal_name,
                payload=e.payload,
            )
            for e in extracted.events
        ]
        return ParseResult(companies=companies, events=events)
