"""
ICP (Ideal Customer Profile) scorer.

Reads a YAML config defining the user's target profile and updates
`companies.target_priority` (0-100) for every active company.

Why a config file rather than hardcoded weights:
  - Different sellers have different ICPs. SAP consultancy wants
    manufacturers >$50M with Korean/Japanese parents. Salesforce wants
    services companies. Cybersecurity wants finance/banking.
  - Re-scoring is cheap and idempotent — change the YAML, re-run, get
    a new prioritized list. No code change.

Score components (default weights, all configurable):
  - recency_factor  (0-25)  — first_licensed within target window
  - investment      (0-20)  — registered capital tier
  - geography       (0-10)  — preferred provinces
  - industry        (0-20)  — preferred industries
  - origin          (0-10)  — preferred parent countries
  - tech_stack      (0-10)  — uses tech we sell to / against
  - signals         (0-15)  — recent hiring, expansion, etc.
  - has_decisionmkr (0-10)  — bonus if we already know an IT contact

Total clipped to 100. Companies that fail a "must_have" gate get 0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent1_schema.models import Company, CompanySignal, Person
from agent1_schema.models.db import session_scope

logger = logging.getLogger(__name__)


@dataclass
class ICPConfig:
    # Hard gates — must all be true for the company to score above 0
    must_have: dict[str, Any] = field(default_factory=dict)
    # Soft scoring — each component contributes to the total
    weights: dict[str, int] = field(default_factory=dict)
    target_industries: list[str] = field(default_factory=list)
    target_countries: list[str] = field(default_factory=list)
    target_provinces: list[str] = field(default_factory=list)
    target_tech: list[str] = field(default_factory=list)  # e.g. ['sap_ecc', 'oracle_ebs']
    investment_tiers: list[dict] = field(default_factory=list)
    recency_window_years: int = 5

    @classmethod
    def from_yaml(cls, path: Path) -> "ICPConfig":
        with open(path) as f:
            raw = yaml.safe_load(f)
        return cls(**raw)

    @classmethod
    def default(cls) -> "ICPConfig":
        """Default ICP — manufacturing FDI, factory in last 5 years."""
        return cls(
            must_have={"vn_province": "any", "first_licensed_within_years": 5},
            weights={
                "recency": 25,
                "investment": 20,
                "geography": 10,
                "industry": 20,
                "origin": 10,
                "tech_stack": 10,
                "signals": 15,
                "has_decisionmkr": 10,
            },
            target_industries=[
                "Electronics manufacturing",
                "Automotive",
                "Semiconductor",
                "Pharmaceutical",
                "Food processing",
                "Textile",
                "Machinery",
            ],
            target_countries=["KR", "JP", "DE", "US", "SG", "TW", "TH", "CN"],
            target_provinces=[
                "Bắc Ninh",
                "Bình Dương",
                "Đồng Nai",
                "Hải Phòng",
                "Long An",
                "Hưng Yên",
                "Hà Nội",
                "Hồ Chí Minh",
            ],
            target_tech=["sap_ecc", "sap_s4hana", "oracle_ebs", "microsoft_dynamics"],
            investment_tiers=[
                {"min_usd": 100_000_000, "score": 20},
                {"min_usd": 50_000_000, "score": 16},
                {"min_usd": 20_000_000, "score": 12},
                {"min_usd": 5_000_000, "score": 6},
                {"min_usd": 0, "score": 2},
            ],
            recency_window_years=5,
        )


def passes_gates(company: Company, cfg: ICPConfig) -> bool:
    must = cfg.must_have or {}
    if "vn_province" in must and must["vn_province"] != "any":
        if company.vn_province != must["vn_province"]:
            return False
    yrs = must.get("first_licensed_within_years")
    if yrs and company.first_licensed:
        cutoff = date.today() - timedelta(days=yrs * 365)
        if company.first_licensed < cutoff:
            return False
    elif yrs and not company.first_licensed:
        # Unknown date — don't filter aggressively, but penalize via recency score
        pass
    return True


def score_company(
    company: Company,
    cfg: ICPConfig,
    *,
    has_decisionmaker: bool = False,
    signal_score: int = 0,
) -> int:
    if not passes_gates(company, cfg):
        return 0

    w = cfg.weights
    total = 0

    # Recency
    if company.first_licensed:
        years_old = (date.today() - company.first_licensed).days / 365
        if years_old <= cfg.recency_window_years:
            recency_pts = w.get("recency", 0) * (
                1 - years_old / cfg.recency_window_years
            )
            total += int(recency_pts)
    # If no date known, give partial recency score so we don't kill it
    else:
        total += w.get("recency", 0) // 4

    # Investment tier
    if company.investment_usd:
        for tier in cfg.investment_tiers:
            if company.investment_usd >= tier["min_usd"]:
                total += min(tier["score"], w.get("investment", 999))
                break

    # Geography
    if company.vn_province in cfg.target_provinces:
        total += w.get("geography", 0)

    # Industry — fuzzy match on label
    if company.industry_label and cfg.target_industries:
        label_lower = company.industry_label.lower()
        if any(ind.lower() in label_lower for ind in cfg.target_industries):
            total += w.get("industry", 0)

    # Origin country
    if company.hq_country in cfg.target_countries:
        total += w.get("origin", 0)

    # Tech stack
    stack = company.tech_stack or {}
    if any(stack.get(t) for t in cfg.target_tech):
        total += w.get("tech_stack", 0)

    # Signals (passed in by caller)
    total += min(signal_score, w.get("signals", 0))

    # Has a decision-maker already
    if has_decisionmaker:
        total += w.get("has_decisionmkr", 0)

    return min(100, max(0, total))


def rescore_all(
    cfg: Optional[ICPConfig] = None, *, dry_run: bool = False
) -> dict[str, int]:
    """Re-score every active company. Returns count by priority bucket."""
    cfg = cfg or ICPConfig.default()
    buckets = {"hot": 0, "warm": 0, "cool": 0, "cold": 0, "skip": 0}

    with session_scope() as s:
        companies = list(
            s.execute(select(Company).where(Company.status == "active")).scalars()
        )

        # Pre-compute decision-maker presence in one query
        dm_ids = set(
            row.company_id
            for row in s.execute(
                select(Person.company_id)
                .where(Person.deleted_at.is_(None))
                .where(
                    Person.role_category.in_(
                        [
                            "cio",
                            "cdo",
                            "cto",
                            "head_of_it",
                            "it_director",
                            "head_of_sap",
                            "erp_manager",
                            "it_manager",
                        ]
                    )
                )
                .distinct()
            )
            if row.company_id
        )

        # Pre-compute signal scores per company
        sig_score: dict = {}
        for sig in s.execute(select(CompanySignal)).scalars():
            sig_score[sig.company_id] = sig_score.get(sig.company_id, 0) + (
                sig.score // 4
            )

        for company in companies:
            score = score_company(
                company,
                cfg,
                has_decisionmaker=company.id in dm_ids,
                signal_score=sig_score.get(company.id, 0),
            )
            if not dry_run:
                company.target_priority = score

            if score >= 75:
                buckets["hot"] += 1
            elif score >= 50:
                buckets["warm"] += 1
            elif score >= 25:
                buckets["cool"] += 1
            elif score > 0:
                buckets["cold"] += 1
            else:
                buckets["skip"] += 1

    logger.info("Rescored %d companies: %s", len(companies), buckets)
    return buckets


def main() -> None:
    import argparse
    import os

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, help="YAML ICP config (uses default if absent)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    cfg = ICPConfig.from_yaml(args.config) if args.config else ICPConfig.default()
    rescore_all(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
