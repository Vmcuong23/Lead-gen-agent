"""Daily runs page — orchestrator history and KPIs."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st


def render() -> None:
    st.title("Daily runs")
    st.caption("Pipeline history from the cron orchestrator.")

    runs = _load_runs()
    if runs.empty:
        st.info(
            "No runs recorded yet. Run `python -m orchestrator.daily_run` "
            "or wait for the cron job to fire."
        )
        return

    last_30 = runs[runs["started_at"] >= datetime.utcnow() - timedelta(days=30)]

    # KPIs across the last 30 days
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Runs (30d)", len(last_30))
    success = (last_30["overall_status"] == "success").sum()
    c2.metric("Success rate (30d)", f"{success / max(1, len(last_30)) * 100:.0f}%")
    c3.metric(
        "Avg duration",
        f"{last_30['duration_min'].mean():.1f} min" if not last_30.empty else "—",
    )
    c4.metric(
        "Total cos discovered (30d)",
        int(last_30["companies_discovered"].sum()),
    )

    st.divider()

    # 30-day chart of companies + people discovered
    if not last_30.empty:
        chart_data = (
            last_30.set_index("started_at")[
                ["companies_discovered", "people_discovered"]
            ]
            .resample("D")
            .sum()
            .fillna(0)
        )
        st.subheader("Last 30 days — daily yield")
        st.bar_chart(chart_data)

    st.divider()
    st.subheader("Recent runs")

    runs_view = runs.head(20).copy()
    runs_view["started_at"] = runs_view["started_at"].dt.strftime("%Y-%m-%d %H:%M")
    runs_view = runs_view[
        [
            "started_at",
            "mode",
            "overall_status",
            "duration_min",
            "companies_discovered",
            "people_discovered",
            "emails_verified",
            "notes",
        ]
    ]

    sel = st.dataframe(
        runs_view,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        height=400,
    )
    if sel and sel.selection.rows:
        run_idx = sel.selection.rows[0]
        run_id = runs.iloc[run_idx]["run_id"]
        _render_run_detail(run_id)


def _render_run_detail(run_id: str) -> None:
    st.divider()
    st.subheader("Run detail")
    stages = _load_run_stages(run_id)
    if stages.empty:
        st.info("No stage detail.")
        return
    st.dataframe(stages, use_container_width=True, hide_index=True)


@st.cache_data(ttl=30)
def _load_runs() -> pd.DataFrame:
    """Load orchestrator runs from DB if present, fall back to local JSONL."""
    try:
        from sqlalchemy import text
        from agent1_schema.models.db import session_scope

        with session_scope() as s:
            # Make sure table exists
            try:
                rows = s.execute(
                    text(
                        """
                        SELECT run_id::text, started_at, finished_at, mode,
                               overall_status, notes, stages
                        FROM orchestrator_runs
                        ORDER BY started_at DESC
                        LIMIT 200
                        """
                    )
                ).all()
            except Exception:
                return pd.DataFrame()

        records = []
        for r in rows:
            stages = r.stages or []
            records.append(_summarize_run(
                run_id=r.run_id,
                started_at=r.started_at,
                finished_at=r.finished_at,
                mode=r.mode,
                overall_status=r.overall_status,
                notes=r.notes or "",
                stages=stages,
            ))
        return pd.DataFrame(records)
    except Exception:
        return _load_from_jsonl()


def _load_from_jsonl() -> pd.DataFrame:
    import json
    import pathlib

    p = pathlib.Path("./run_log.jsonl")
    if not p.exists():
        return pd.DataFrame()
    records = []
    with p.open() as f:
        for line in f:
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(_summarize_run(
                run_id=run["run_id"],
                started_at=datetime.fromisoformat(run["started_at"]),
                finished_at=(
                    datetime.fromisoformat(run["finished_at"])
                    if run.get("finished_at")
                    else None
                ),
                mode=run["mode"],
                overall_status=run["overall_status"],
                notes=run.get("notes", ""),
                stages=run.get("stages", []),
            ))
    return pd.DataFrame(records).sort_values("started_at", ascending=False)


def _summarize_run(*, run_id, started_at, finished_at, mode,
                    overall_status, notes, stages: list[dict]) -> dict:
    duration_min = (
        (finished_at - started_at).total_seconds() / 60
        if finished_at and started_at
        else 0
    )

    def _achieved(name_substr: str) -> int:
        for s in stages:
            if name_substr in s.get("name", ""):
                return s.get("achieved", 0)
        return 0

    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "mode": mode,
        "overall_status": overall_status,
        "duration_min": round(duration_min, 1),
        "companies_discovered": _achieved("discover_companies"),
        "people_discovered": _achieved("discover_people"),
        "emails_verified": _achieved("verify_emails"),
        "notes": notes,
    }


@st.cache_data(ttl=30)
def _load_run_stages(run_id: str) -> pd.DataFrame:
    """Load per-stage details for one run."""
    try:
        from sqlalchemy import text
        from agent1_schema.models.db import session_scope

        with session_scope() as s:
            row = s.execute(
                text(
                    "SELECT stages FROM orchestrator_runs WHERE run_id = :id"
                ),
                {"id": run_id},
            ).first()
        if not row or not row.stages:
            return pd.DataFrame()
        return pd.DataFrame(row.stages)
    except Exception:
        # JSONL fallback
        import json
        import pathlib

        p = pathlib.Path("./run_log.jsonl")
        if not p.exists():
            return pd.DataFrame()
        with p.open() as f:
            for line in f:
                try:
                    run = json.loads(line)
                    if run["run_id"] == run_id:
                        return pd.DataFrame(run.get("stages", []))
                except json.JSONDecodeError:
                    continue
        return pd.DataFrame()
