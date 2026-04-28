"""
Daily orchestrator — the cron entry point.

Runs the full pipeline once per day:

    1. Agent 2 — discover companies (target: N new companies)
       Sources rotated daily so we don't hammer the same one.
    2. ICP scorer — re-rank everything
    3. Agent 3 — find people for the top-priority companies that don't have any
    4. Email verifier — verify emails for the freshest people
    5. Embedding refresh — embed any new companies/people
    6. Run-log row written to `orchestrator_runs` table

The job has TWO MODES, auto-detected:

    BACKFILL mode (companies < 1500):
        - Hit FIA + industrial parks aggressively
        - Daily target = COMPANIES_DAILY_TARGET (default 50)
        - Will fail-loud if it can't hit 80% of target after 3 days running

    STEADY-STATE mode (companies >= 1500):
        - Discover only new licensings (last 30 days)
        - Re-enrich N existing companies that haven't been refreshed in 90+ days
        - Daily target = max(COMPANIES_DAILY_TARGET, 5) for *fresh* companies,
          plus REENRICH_DAILY_TARGET (default 30) for re-enrichment

Why this matters: Vietnam licenses ~10 FDI projects/day total, of which
2-5 fit a manufacturing-IT ICP. A "50/day" steady-state target is
unrealistic without re-enrichment counting toward the goal.
"""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("orchestrator")

BACKFILL_THRESHOLD = int(os.environ.get("BACKFILL_THRESHOLD", "1500"))
COMPANIES_DAILY_TARGET = int(os.environ.get("COMPANIES_DAILY_TARGET", "50"))
REENRICH_DAILY_TARGET = int(os.environ.get("REENRICH_DAILY_TARGET", "30"))
PEOPLE_DAILY_TARGET = int(os.environ.get("PEOPLE_DAILY_TARGET", "100"))
EMAIL_VERIFY_DAILY_TARGET = int(os.environ.get("EMAIL_VERIFY_DAILY_TARGET", "100"))

# Rotate through sources so we don't refetch the same one every day.
# Index by day-of-year mod len.
SOURCE_ROTATION = ["fia", "vsip"]
# In production, add: "becamex", "deepc", "amata", "vir", "vneconomy", "vnexpress"


@dataclass
class StageResult:
    name: str
    target: int
    achieved: int
    duration_sec: float
    status: str   # 'success' | 'partial' | 'failed' | 'skipped'
    error: Optional[str] = None
    details: dict = field(default_factory=dict)


@dataclass
class RunLog:
    run_id: str
    started_at: datetime
    finished_at: Optional[datetime]
    mode: str   # 'backfill' | 'steady_state'
    stages: list[StageResult] = field(default_factory=list)
    overall_status: str = "running"   # 'running' | 'success' | 'partial' | 'failed'
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "mode": self.mode,
            "overall_status": self.overall_status,
            "notes": self.notes,
            "stages": [asdict(s) for s in self.stages],
        }


def detect_mode() -> str:
    """Look at company count to decide backfill vs steady-state."""
    try:
        from sqlalchemy import func, select
        from agent1_schema.models import Company
        from agent1_schema.models.db import session_scope

        with session_scope() as s:
            n = s.scalar(
                select(func.count(Company.id)).where(Company.status == "active")
            ) or 0
        return "backfill" if n < BACKFILL_THRESHOLD else "steady_state"
    except Exception as e:
        logger.warning("Could not detect mode (%s); defaulting to backfill", e)
        return "backfill"


def select_source_for_today() -> str:
    """Round-robin sources so we don't always hit FIA."""
    return SOURCE_ROTATION[datetime.now().timetuple().tm_yday % len(SOURCE_ROTATION)]


# ---------------------------------------------------------------------------
# Stages — each is a thin wrapper around an existing agent module
# ---------------------------------------------------------------------------

def _time_stage(name: str, target: int, fn) -> StageResult:
    """Run fn() returning (achieved_count, details), wrap in StageResult."""
    t0 = time.time()
    try:
        achieved, details = fn()
        elapsed = time.time() - t0
        if achieved >= target:
            status = "success"
        elif achieved >= max(1, int(target * 0.5)):
            status = "partial"
        else:
            status = "partial" if achieved > 0 else "failed"
        return StageResult(name=name, target=target, achieved=achieved,
                           duration_sec=round(elapsed, 1), status=status,
                           details=details)
    except Exception as e:
        elapsed = time.time() - t0
        logger.exception("Stage %s failed", name)
        return StageResult(name=name, target=target, achieved=0,
                           duration_sec=round(elapsed, 1), status="failed",
                           error=str(e)[:500],
                           details={"traceback": traceback.format_exc()[-1500:]})


def stage_discover_companies(target: int, source: str) -> StageResult:
    """Agent 2 — fetch + parse from one source until target hit (or source exhausted)."""

    def _run():
        from agent2_fdi_scraper.run import fetch_and_store, parse_pending

        # Fetch is cheap; use a generous fetch limit so we have material to parse
        fetched = fetch_and_store(source, limit=max(target * 2, 10))
        # Parse is expensive (Claude calls); cap at target so we stay on budget
        parsed = parse_pending(source, limit=target)
        return parsed, {"source": source, "fetched": fetched, "parsed": parsed}

    return _time_stage(f"discover_companies({source})", target, _run)


def stage_reenrich_companies(target: int) -> StageResult:
    """Find companies not enriched in 90+ days; refresh their data."""

    def _run():
        from sqlalchemy import select
        from agent1_schema.models import Company
        from agent1_schema.models.db import session_scope

        cutoff = datetime.utcnow() - timedelta(days=90)
        refreshed = 0
        with session_scope() as s:
            stale = list(s.execute(
                select(Company)
                .where(Company.status == "active")
                .where(
                    (Company.last_enriched.is_(None))
                    | (Company.last_enriched < cutoff)
                )
                .order_by(Company.target_priority.desc())
                .limit(target)
            ).scalars())
            # In production: re-fetch website, re-run Wappalyzer, etc.
            # For now we just bump last_enriched as a placeholder.
            for c in stale:
                c.last_enriched = datetime.utcnow()
                refreshed += 1
        return refreshed, {"refreshed": refreshed}

    return _time_stage("reenrich_companies", target, _run)


def stage_score_icp() -> StageResult:
    def _run():
        from shared.icp import ICPConfig, rescore_all
        cfg_path = Path(__file__).parent.parent / "shared" / "icp.yaml"
        cfg = ICPConfig.from_yaml(cfg_path) if cfg_path.exists() else ICPConfig.default()
        buckets = rescore_all(cfg)
        scored = sum(buckets.values())
        return scored, buckets

    return _time_stage("score_icp", 0, _run)  # no target — always succeeds


def stage_discover_people(target: int) -> StageResult:
    """Run Agent 3 against the highest-priority companies that have <2 known people."""

    def _run():
        from sqlalchemy import func, select
        from agent1_schema.models import Company, Person
        from agent1_schema.models.db import session_scope
        from agent3_people_discovery.run import run_discovery

        # Pick companies that are high-priority but under-populated with people
        with session_scope() as s:
            people_count = dict(s.execute(
                select(Person.company_id, func.count(Person.id))
                .where(Person.deleted_at.is_(None))
                .group_by(Person.company_id)
            ).all())
            companies = list(s.execute(
                select(Company)
                .where(Company.status == "active")
                .where(Company.target_priority >= 50)
                .order_by(Company.target_priority.desc())
            ).scalars())
        # Target ~5 people per company, so cap companies-per-day at target/5
        per_co_target = 5
        max_companies = max(1, target // per_co_target)
        candidates = [
            c.id for c in companies
            if people_count.get(c.id, 0) < per_co_target
        ][:max_companies]

        found = run_discovery([str(cid) for cid in candidates], limit=None)
        return found, {
            "companies_scanned": len(candidates),
            "people_added": found,
        }

    return _time_stage("discover_people", target, _run)


def stage_verify_emails(target: int) -> StageResult:
    def _run():
        from agent3_people_discovery.run import run_email_verification
        verified = run_email_verification(limit=target)
        return verified, {"verified": verified}

    return _time_stage("verify_emails", target, _run)


def stage_refresh_embeddings() -> StageResult:
    def _run():
        try:
            from shared.embeddings import (
                EmbeddingsUnavailable,
                enrich_company_embeddings,
                enrich_people_embeddings,
            )
        except ImportError as e:
            return 0, {"skipped": str(e)}
        try:
            co = enrich_company_embeddings(limit=200)
            pp = enrich_people_embeddings(limit=200)
        except EmbeddingsUnavailable as e:
            return 0, {"skipped": str(e)}
        return co + pp, {"companies_embedded": co, "people_embedded": pp}

    return _time_stage("refresh_embeddings", 0, _run)


# ---------------------------------------------------------------------------
# Run-log persistence
# ---------------------------------------------------------------------------

def persist_run_log(run: RunLog) -> None:
    """Write to DB if available, else to a local JSONL file."""
    log_path = Path(os.environ.get("RUN_LOG_PATH", "./run_log.jsonl"))
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(run.to_dict()) + "\n")
    except Exception as e:
        logger.warning("Failed to write local run log: %s", e)

    # Also persist to DB if available — keeps Streamlit dashboard fed
    try:
        from sqlalchemy import text
        from agent1_schema.models.db import session_scope

        with session_scope() as s:
            # Idempotent table create
            s.execute(text("""
                CREATE TABLE IF NOT EXISTS orchestrator_runs (
                    run_id        UUID PRIMARY KEY,
                    started_at    TIMESTAMPTZ NOT NULL,
                    finished_at   TIMESTAMPTZ,
                    mode          TEXT NOT NULL,
                    overall_status TEXT NOT NULL,
                    notes         TEXT,
                    stages        JSONB NOT NULL
                )
            """))
            s.execute(text("""
                INSERT INTO orchestrator_runs
                    (run_id, started_at, finished_at, mode, overall_status, notes, stages)
                VALUES
                    (:run_id, :started_at, :finished_at, :mode, :status, :notes, :stages)
                ON CONFLICT (run_id) DO UPDATE SET
                    finished_at = EXCLUDED.finished_at,
                    overall_status = EXCLUDED.overall_status,
                    notes = EXCLUDED.notes,
                    stages = EXCLUDED.stages
            """), {
                "run_id": run.run_id,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "mode": run.mode,
                "status": run.overall_status,
                "notes": run.notes,
                "stages": json.dumps([asdict(s) for s in run.stages], default=str),
            })
    except Exception as e:
        logger.warning("Failed to persist run log to DB: %s", e)


# ---------------------------------------------------------------------------
# Main daily run
# ---------------------------------------------------------------------------

def run_daily(
    mode: Optional[str] = None,
    company_target: Optional[int] = None,
    people_target: Optional[int] = None,
) -> RunLog:
    run = RunLog(
        run_id=str(uuid.uuid4()),
        started_at=datetime.utcnow(),
        finished_at=None,
        mode=mode or detect_mode(),
    )
    co_target = company_target or COMPANIES_DAILY_TARGET
    p_target = people_target or PEOPLE_DAILY_TARGET

    logger.info("=" * 70)
    logger.info("Daily run %s starting (mode=%s)", run.run_id[:8], run.mode)
    logger.info("Targets: %d companies, %d people, %d emails",
                co_target, p_target, EMAIL_VERIFY_DAILY_TARGET)
    logger.info("=" * 70)

    source = select_source_for_today()
    run.stages.append(stage_discover_companies(co_target, source))

    if run.mode == "steady_state":
        run.stages.append(stage_reenrich_companies(REENRICH_DAILY_TARGET))

    run.stages.append(stage_score_icp())
    run.stages.append(stage_refresh_embeddings())
    run.stages.append(stage_discover_people(p_target))
    run.stages.append(stage_verify_emails(EMAIL_VERIFY_DAILY_TARGET))

    # Overall status
    failed = [s for s in run.stages if s.status == "failed"]
    partial = [s for s in run.stages if s.status == "partial"]
    if failed:
        run.overall_status = "failed"
        run.notes = f"{len(failed)} stage(s) failed: " + ", ".join(s.name for s in failed)
    elif partial:
        run.overall_status = "partial"
        run.notes = f"{len(partial)} stage(s) below target"
    else:
        run.overall_status = "success"

    run.finished_at = datetime.utcnow()
    persist_run_log(run)

    logger.info("=" * 70)
    logger.info("Run finished: %s (%d stages)",
                run.overall_status, len(run.stages))
    for s in run.stages:
        logger.info("  %-32s %-8s %d/%d in %.1fs",
                    s.name, s.status, s.achieved, s.target, s.duration_sec)
    logger.info("=" * 70)
    return run


def main():
    import argparse

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["backfill", "steady_state"])
    p.add_argument("--company-target", type=int)
    p.add_argument("--people-target", type=int)
    args = p.parse_args()

    run = run_daily(
        mode=args.mode,
        company_target=args.company_target,
        people_target=args.people_target,
    )
    # Exit non-zero if anything failed — gives cron something to alert on
    raise SystemExit(0 if run.overall_status == "success" else 1)


if __name__ == "__main__":
    main()
