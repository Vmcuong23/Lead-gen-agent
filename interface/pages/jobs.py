"""Jobs page — trigger pipeline runs from the dashboard."""
from __future__ import annotations

import subprocess
import sys

import streamlit as st


def render() -> None:
    st.title("Run jobs")
    st.caption("Kick off pipeline tasks. Output streams to your terminal.")

    st.markdown("### Agent 2 — FDI scraper")
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        source = st.selectbox("Source", ["fia", "vsip"])
    with c2:
        limit = st.number_input("Limit", min_value=1, value=5)
    with c3:
        if st.button("▶ Discover + parse", key="agent2"):
            _run(["python", "-m", "agent2_fdi_scraper.run", "--source", source, "--limit", str(limit)])

    st.markdown("### Agent 3 — People discovery")
    c1, c2 = st.columns([3, 1])
    with c1:
        people_limit = st.number_input("Companies to scan", min_value=1, value=10, key="p_lim")
    with c2:
        if st.button("▶ Find people", key="agent3"):
            _run(["python", "-m", "agent3_people_discovery.run", "--limit", str(people_limit)])

    c3, c4 = st.columns([3, 1])
    with c3:
        verify_limit = st.number_input("People to verify", min_value=1, value=20, key="v_lim")
    with c4:
        if st.button("▶ Verify emails", key="verify"):
            _run([
                "python", "-m", "agent3_people_discovery.run",
                "--verify-emails", "--limit", str(verify_limit)
            ])

    st.markdown("### ICP scorer")
    if st.button("▶ Re-score all companies"):
        _run(["python", "-m", "shared.icp"])

    st.markdown("### Embeddings")
    c5, c6 = st.columns(2)
    with c5:
        if st.button("▶ Embed companies"):
            _run([sys.executable, "-c",
                  "from shared.embeddings import enrich_company_embeddings;"
                  " print(enrich_company_embeddings())"])
    with c6:
        if st.button("▶ Embed people"):
            _run([sys.executable, "-c",
                  "from shared.embeddings import enrich_people_embeddings;"
                  " print(enrich_people_embeddings())"])


def _run(cmd: list[str]) -> None:
    st.info("Running: `" + " ".join(cmd) + "`")
    placeholder = st.empty()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        log: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            log.append(line.rstrip())
            placeholder.code("\n".join(log[-40:]))
        proc.wait()
        if proc.returncode == 0:
            st.success("✓ Job completed successfully.")
        else:
            st.error(f"Job exited with code {proc.returncode}.")
    except Exception as e:
        st.error(f"Failed to run: {e}")
