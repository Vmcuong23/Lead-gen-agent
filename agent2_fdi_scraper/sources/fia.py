"""
FIA / MPI monthly FDI bulletin source.

The Foreign Investment Agency (https://fia.mpi.gov.vn) publishes monthly
reports listing newly-licensed foreign investment projects. This source
fetches the index page, parses out PDF links to recent bulletins, and
yields each PDF as a FetchedDoc.

The actual structured-data extraction happens in `parsers.fia_pdf_parser`,
which feeds the PDF to Claude with a strict JSON schema.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from agent2_fdi_scraper.base import FetchedDoc, Source

logger = logging.getLogger(__name__)

INDEX_URL = "https://fia.mpi.gov.vn/Detail/CatID/457641e2-2605-4632-bbd8-39ee65454a06"
USER_AGENT = (
    "Mozilla/5.0 (compatible; FDIAgent/0.1; "
    "+https://github.com/example/fdi-agent)"
)


class FIASource(Source):
    slug = "fia_mpi"

    def __init__(self, since_year: int = 2021, http: httpx.Client | None = None):
        self.since_year = since_year
        self._http = http or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=60.0,
            follow_redirects=True,
        )

    def discover(self) -> Iterator[FetchedDoc]:
        logger.info("Fetching FIA index: %s", INDEX_URL)
        resp = self._http.get(INDEX_URL)
        resp.raise_for_status()

        for pdf_url, label in self._extract_pdf_links(resp.text, INDEX_URL):
            year_match = re.search(r"(20\d{2})", label)
            if year_match and int(year_match.group(1)) < self.since_year:
                continue

            try:
                pdf_resp = self._http.get(pdf_url)
                pdf_resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("Failed to fetch %s: %s", pdf_url, e)
                continue

            yield FetchedDoc(
                url=pdf_url,
                content_type="application/pdf",
                content=pdf_resp.content,
                source_slug=self.slug,
            )

    @staticmethod
    def _extract_pdf_links(html: str, base_url: str) -> Iterator[tuple[str, str]]:
        """Yield (absolute_url, link_text) for every PDF link on the page."""
        tree = HTMLParser(html)
        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            if not href.lower().endswith(".pdf"):
                continue
            abs_url = urljoin(base_url, href)
            text = (a.text() or "").strip()
            yield abs_url, text
