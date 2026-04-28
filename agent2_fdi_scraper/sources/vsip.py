"""
VSIP industrial park tenant source.

VSIP (Vietnam Singapore Industrial Park) operates parks in Bình Dương,
Bắc Ninh, Hải Phòng, Quảng Ngãi, Nghệ An, Hải Dương, and Cần Thơ. Many
park websites list tenants with company name, country, and industry —
exactly what we need.

This is illustrative; structure varies by park. In production, we'd
have one Source subclass per park (VSIPSource, BecamexSource, DEEPCSource).
"""
from __future__ import annotations

import logging
from typing import Iterator

import httpx

from agent2_fdi_scraper.base import FetchedDoc, Source

logger = logging.getLogger(__name__)

# Pages with tenant directories (verified manually). Add more as discovered.
TENANT_PAGES = [
    "https://vsip.com.vn/our-parks/vsip-bac-ninh.html",
    "https://vsip.com.vn/our-parks/vsip-hai-phong.html",
    "https://vsip.com.vn/our-parks/vsip-binh-duong.html",
]


class VSIPSource(Source):
    slug = "vsip"

    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(
            headers={"User-Agent": "Mozilla/5.0 (compatible; FDIAgent/0.1)"},
            timeout=30.0,
            follow_redirects=True,
        )

    def discover(self) -> Iterator[FetchedDoc]:
        for url in TENANT_PAGES:
            try:
                resp = self._http.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("VSIP fetch failed %s: %s", url, e)
                continue

            yield FetchedDoc(
                url=url,
                content_type="text/html",
                content=resp.content,
                source_slug=self.slug,
            )
