"""
Agent 2 runner — entry point.

    python -m agent2_fdi_scraper.run --source fia --limit 5
    python -m agent2_fdi_scraper.run --source vsip
    python -m agent2_fdi_scraper.run --reparse  # re-run parsers on stored raw docs
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
from typing import Iterable

from sqlalchemy import select

from agent1_schema.models import RawDocument, Source
from agent1_schema.models.db import session_scope
from agent2_fdi_scraper.base import FetchedDoc, Parser
from agent2_fdi_scraper.parsers.fia_pdf_parser import FIAPDFParser
from agent2_fdi_scraper.parsers.park_html_parser import IndustrialParkHTMLParser
from agent2_fdi_scraper.sources.fia import FIASource
from agent2_fdi_scraper.sources.vsip import VSIPSource
from agent2_fdi_scraper.upsert import (
    store_raw_document,
    upsert_company,
    upsert_event,
)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("agent2")

RAW_STORE = pathlib.Path(os.environ.get("RAW_STORE", "./raw_docs"))
RAW_STORE.mkdir(exist_ok=True)

SOURCES = {
    "fia": (FIASource, FIAPDFParser),
    "vsip": (VSIPSource, IndustrialParkHTMLParser),
}


def fetch_and_store(source_name: str, limit: int | None) -> int:
    SourceCls, _ = SOURCES[source_name]
    source = SourceCls()
    count = 0
    for doc in source.discover():
        if limit and count >= limit:
            break
        _persist_raw(doc)
        count += 1
    logger.info("Fetched %d new docs from %s", count, source_name)
    return count


def _persist_raw(doc: FetchedDoc) -> None:
    """Store blob to disk + raw_documents row."""
    h = doc.content_hash
    ext = ".pdf" if doc.content_type == "application/pdf" else ".html"
    blob_path = RAW_STORE / f"{doc.source_slug}_{h[:16]}{ext}"
    if not blob_path.exists():
        blob_path.write_bytes(doc.content)
    with session_scope() as s:
        store_raw_document(
            s,
            source_slug=doc.source_slug,
            url=doc.url,
            content_hash=h,
            content_type=doc.content_type,
            storage_path=str(blob_path),
        )


def parse_pending(source_name: str | None, limit: int | None) -> int:
    """Run parsers over unparsed raw_documents."""
    parsers: dict[str, Parser] = {
        slug: parser_cls()
        for slug, (_, parser_cls) in SOURCES.items()
        if not source_name or slug == source_name
    }

    parsed = 0
    with session_scope() as s:
        q = (
            select(RawDocument, Source)
            .join(Source, RawDocument.source_id == Source.id)
            .where(RawDocument.parsed == False)  # noqa: E712
        )
        if source_name:
            q = q.where(Source.slug == source_name)
        if limit:
            q = q.limit(limit)
        rows = s.execute(q).all()

    for doc, src in rows:
        parser = parsers.get(src.slug)
        if not parser:
            continue
        try:
            content = pathlib.Path(doc.storage_path).read_bytes()
        except Exception as e:
            logger.warning("Cannot read %s: %s", doc.storage_path, e)
            continue

        try:
            result = parser.parse(content, doc.content_type, doc.url)
        except Exception as e:
            logger.exception("Parse failed for %s", doc.url)
            with session_scope() as s:
                d = s.get(RawDocument, doc.id)
                if d:
                    d.parse_error = str(e)[:1000]
            continue

        with session_scope() as s:
            d = s.get(RawDocument, doc.id)
            if d is None:
                continue
            company_by_legal = {}
            for c_rec in result.companies:
                company = upsert_company(s, c_rec, d)
                company_by_legal[c_rec.legal_name] = company
            for e_rec in result.events:
                company = company_by_legal.get(e_rec.company_legal_name)
                if company:
                    upsert_event(s, e_rec, company, d)
            d.parsed = True
            parsed += 1
            logger.info(
                "Parsed %s: %d companies, %d events",
                doc.url[:80],
                len(result.companies),
                len(result.events),
            )

    return parsed


def main(argv: Iterable[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=list(SOURCES))
    p.add_argument("--limit", type=int)
    p.add_argument("--fetch-only", action="store_true")
    p.add_argument("--parse-only", action="store_true")
    args = p.parse_args(argv)

    if not args.parse_only:
        if not args.source:
            p.error("--source required unless --parse-only")
        fetch_and_store(args.source, args.limit)

    if not args.fetch_only:
        parse_pending(args.source, args.limit)


if __name__ == "__main__":
    main()
