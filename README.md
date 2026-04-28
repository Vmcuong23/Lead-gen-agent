# Lead-gen-agent

Personal AI agent for finding **IT decision-makers at FDI companies in Vietnam**.

Targets: CIO / CDO / CTO / Head of IT / IT Director / Head of SAP / ERP Manager / IT Manager
at foreign-invested manufacturers with factory plants set up in the last 5 years.

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Postgres (Neon free tier)                     │
└──────────────────────────────────────────────────────────────────────┘
        ▲              ▲                ▲                ▲
   ┌────┴───┐    ┌─────┴────┐    ┌──────┴─────┐   ┌──────┴───────┐
   │Agent 1 │    │ Agent 2  │    │  Agent 3   │   │   Shared     │
   │Schema  │    │FDI       │    │  People    │   │  - embeddings│
   │Bootstrap│    │Scraper   │    │  Discovery │   │  - ICP scorer│
   └────────┘    └──────────┘    └────────────┘   └──────────────┘
                                                          │
                  ┌───────────────────────────────────────┴─┐
                  │  Streamlit Cloud (dashboard)             │
                  │  GitHub Actions (daily cron)             │
                  └──────────────────────────────────────────┘
```

## ⚡ 15-minute setup

### Step 1 — Set up Neon Postgres (3 min)

```bash
npx neonctl@latest auth        # opens browser to log in
npx neonctl@latest projects create --name lead-gen-agent
npx neonctl@latest connection-string
# Copy the URL — looks like: postgresql://user:pwd@ep-xxx.../neondb?sslmode=require
```

### Step 2 — Get API keys (3 min)

- **Anthropic** (required for parsing): https://console.anthropic.com → API keys → Create Key
- **Brave Search** (required for finding people): https://brave.com/search/api/ → free tier = 2,000 queries/month

### Step 3 — Run the bootstrap (5 min)

Download this repo's bundle (or `git clone` if it's already on your laptop), then:

```bash
cd lead-gen-agent
chmod +x bootstrap.sh
./bootstrap.sh
```

The script:
1. Connects to Neon and applies the schema migration
2. Initializes git, adds your repo as the remote, pushes everything
3. Prints exact next steps with your DATABASE_URL pre-filled

### Step 4 — Add GitHub Actions secrets (2 min)

Go to **https://github.com/Vmcuong23/Lead-gen-agent/settings/secrets/actions** and add:

| Secret name | Value |
|---|---|
| `DATABASE_URL` | (from step 1, with `postgresql+psycopg://` prefix) |
| `ANTHROPIC_API_KEY` | (from step 2) |
| `BRAVE_SEARCH_API_KEY` | (from step 2) |
| `SLACK_WEBHOOK_URL` | optional — for failure alerts |

### Step 5 — Deploy to Streamlit Cloud (2 min)

1. Go to https://share.streamlit.io
2. Sign in with your GitHub account (`Vmcuong23`)
3. **New app**:
   - Repository: `Vmcuong23/Lead-gen-agent`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
   - App URL: pick whatever — e.g. `lead-gen-agent`
4. **Advanced settings → Secrets**, paste:
   ```toml
   DATABASE_URL = "postgresql+psycopg://user:pwd@ep-xxx.../neondb?sslmode=require"
   ANTHROPIC_API_KEY = "sk-ant-..."
   BRAVE_SEARCH_API_KEY = "..."
   ANTHROPIC_MODEL = "claude-sonnet-4-5"
   ```
5. **Deploy** — wait ~3 min — your URL is live: `https://<app-name>.streamlit.app`

### Step 6 — Trigger first run (last step)

Go to **https://github.com/Vmcuong23/Lead-gen-agent/actions/workflows/daily-orchestrator.yml**

Click **"Run workflow"** → mode = `backfill` → companies target = `50` → green button.

After ~10 min, refresh your Streamlit URL — companies appear, then people appear.

The cron is now scheduled to run automatically every day at **02:00 Vietnam time** (19:00 UTC).

---

## How the daily target works

Vietnam licenses ~10 FDI projects/day total; after ICP filtering, ~2-5 qualify. The orchestrator handles this honestly:

- **Backfill mode** (companies < 1500): hits FIA + industrial parks aggressively → 50/day target
- **Steady-state mode** (companies ≥ 1500): drops new-company target to ~5/day, picks up re-enrichment of existing companies and aggressive people-discovery

You can override targets per-run in the GitHub Actions UI, or edit them as repo variables under **Settings → Secrets and variables → Actions → Variables**.

## Manually running the orchestrator

```bash
# From your laptop, with DATABASE_URL set
export DATABASE_URL='postgresql+psycopg://...'
export ANTHROPIC_API_KEY='sk-ant-...'
export BRAVE_SEARCH_API_KEY='...'
pip install -r requirements.txt
python -m orchestrator.daily_run --mode backfill --company-target 50
```

## Project layout

```
lead-gen-agent/
├── streamlit_app.py                 # Streamlit Cloud entry point
├── bootstrap.sh                     # one-shot setup
├── requirements.txt                 # for Streamlit Cloud + GH Actions
├── .github/workflows/
│   ├── daily-orchestrator.yml       # cron job
│   └── ci.yml                       # syntax + tests
├── .streamlit/
│   ├── config.toml                  # theme
│   └── secrets.toml.example         # local dev template
├── agent1_schema/                   # Postgres schema + ORM
├── agent2_fdi_scraper/              # FIA + industrial parks
├── agent3_people_discovery/         # SERP + company sites + email verify
├── shared/
│   ├── icp.py                       # ICP scorer
│   ├── icp.yaml                     # ICP config (edit to retune)
│   └── embeddings.py                # fuzzy name matching
├── interface/                       # Streamlit pages
├── orchestrator/
│   └── daily_run.py                 # the cron entry point
├── DEPLOYMENT.md                    # alternative hosting options
└── docker-compose.yml               # for self-hosting
```

## Legal posture

This tool processes personal data of Vietnamese individuals, governed by Vietnam's PDPL (Law 91/2025/QH15, in force since 1 January 2026).

Compliance choices baked into the codebase:
- No LinkedIn scraping — Agent 3 reads search-engine result pages only
- Provenance everywhere — every fact has a source-document audit trail
- Erasure-ready — soft-delete clears email/phone/LinkedIn while preserving audit logs
- Purpose declared — each `people` row carries `purpose` and `legal_basis` columns

Before going to production for outreach, file a DPIA with the Ministry of Public Security (A05) and a CTIA if you transfer data outside Vietnam.

## Costs

Run cost at 50 companies/day, 100 people/day (steady state):

| Item | Daily | Monthly |
|---|---|---|
| Anthropic API (Claude Sonnet 4.5) | ~$0.50 | ~$15 |
| Brave Search API | free tier covers it | $0 |
| Neon Postgres | free tier covers <3GB | $0 |
| GitHub Actions | free for public repos | $0 |
| Streamlit Cloud | free tier | $0 |
| **Total** | **~$0.50** | **~$15** |

If you go private + need more disk, add ~$5/mo Neon Pro and ~$0.008/min for Actions.
