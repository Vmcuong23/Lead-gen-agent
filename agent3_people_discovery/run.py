"""
Agent 3 runner.

    # Discover people for top-priority companies
    python -m agent3_people_discovery.run --limit 50

    # Discover for a specific company
    python -m agent3_people_discovery.run --company-id <uuid>

    # Run email verification on people who have inferred patterns
    python -m agent3_people_discovery.run --verify-emails
"""
from __future__ import annotations

import argparse
import logging
import os
from typing import Iterable
from urllib.parse import urlparse

from sqlalchemy import select

from agent1_schema.models import Company, Person
from agent1_schema.models.db import session_scope
from agent3_people_discovery.base import PeopleRecord
from agent3_people_discovery.extractors.email_finder import (
    EmailVerifier,
    best_email,
)
from agent3_people_discovery.sources.company_website import CompanyWebsiteSource
from agent3_people_discovery.sources.serp import SERPDiscoverySource
from agent3_people_discovery.upsert import upsert_person

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("agent3")


def run_discovery(company_ids: list[str] | None, limit: int | None) -> int:
    """Run all DiscoverySource implementations against target companies."""
    sources = []
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        try:
            sources.append(SERPDiscoverySource())
        except Exception as e:
            logger.warning("SERP source unavailable: %s", e)
    sources.append(CompanyWebsiteSource())

    found = 0
    with session_scope() as s:
        q = select(Company).where(Company.status == "active")
        if company_ids:
            q = q.where(Company.id.in_(company_ids))
        else:
            q = q.order_by(Company.target_priority.desc())
        if limit:
            q = q.limit(limit)
        companies = s.execute(q).scalars().all()
        company_ids_local = [c.id for c in companies]

    for cid in company_ids_local:
        with session_scope() as s:
            company = s.get(Company, cid)
            if not company:
                continue
            for src in sources:
                try:
                    for record in src.find_people(company):
                        upsert_person(s, record, company)
                        found += 1
                except Exception:
                    logger.exception("Source %s failed for %s", src.slug, company.display_name)
            logger.info(
                "Done with %s — total people upserts so far: %d",
                company.display_name,
                found,
            )
    return found


def run_email_verification(limit: int | None) -> int:
    """Find people without verified email; infer + verify."""
    verifier = EmailVerifier(
        sender_addr=os.environ.get("VERIFY_SENDER_ADDR", "verify@example.com")
    )
    verified = 0

    with session_scope() as s:
        q = (
            select(Person)
            .where(Person.deleted_at.is_(None))
            .where(
                (Person.email.is_(None))
                | (Person.email_status.in_(["unverified", "pattern_inferred"]))
            )
        )
        if limit:
            q = q.limit(limit)
        people = list(s.execute(q).scalars())

    for person in people:
        with session_scope() as s:
            person = s.get(Person, person.id)
            if not person or not person.company_id:
                continue
            company = s.get(Company, person.company_id)
            if not company or not company.website:
                continue
            domain = _domain_of(company.website)
            if not domain:
                continue
            result = best_email(person.full_name, domain, verifier)
            if result is None:
                continue
            person.email = result.email
            person.email_status = result.status
            person.email_confidence = result.confidence
            verified += 1
            logger.info(
                "Verified %s -> %s [%s, %d]",
                person.full_name,
                result.email,
                result.status,
                result.confidence,
            )
    return verified


def _domain_of(url: str) -> str | None:
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc or None


def main(argv: Iterable[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--company-id", action="append", help="UUID of a target company")
    p.add_argument("--limit", type=int)
    p.add_argument("--verify-emails", action="store_true")
    args = p.parse_args(argv)

    if args.verify_emails:
        run_email_verification(args.limit)
    else:
        run_discovery(args.company_id, args.limit)


if __name__ == "__main__":
    main()
