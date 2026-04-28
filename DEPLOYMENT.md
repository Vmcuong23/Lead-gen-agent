# Deployment Guide — Getting a Live URL

You have three realistic options for hosting the dashboard. Pick by budget
and audience:

| Option | Cost | URL type | DB included? | Best for |
|---|---|---|---|---|
| Streamlit Community Cloud | Free | `https://*.streamlit.app` | ❌ external | Solo / public demo |
| Railway | ~$5/mo | `https://*.up.railway.app` (custom domain optional) | ✅ Postgres add-on | Solo / small team |
| Tailscale + your own machine | Free | `http://machine:8501` (private) | ✅ local | You only |

A managed Postgres is **required** for Streamlit Cloud — it has no
persistent storage. Cheapest options: **Neon** (free tier, ~3 GB),
**Supabase** (free tier, 500 MB), or **Railway Postgres** (paid).

---

## Option A — Streamlit Cloud (recommended for first deploy)

You'll have a public URL in about 10 minutes.

### 1. Set up managed Postgres on Neon (free)

1. Go to https://neon.tech, sign up
2. Create a new project, pick the region closest to Vietnam (Singapore = `ap-southeast-1`)
3. Copy the connection string — it looks like:
   ```
   postgresql://user:pwd@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
4. Convert to SQLAlchemy format (replace `postgresql://` → `postgresql+psycopg://`):
   ```
   postgresql+psycopg://user:pwd@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```

### 2. Run migrations once against Neon

From your laptop:

```bash
export DATABASE_URL='postgresql+psycopg://user:pwd@ep-xxx.../neondb?sslmode=require'
PSQL_URL="${DATABASE_URL/+psycopg/}"
psql "$PSQL_URL" -f agent1_schema/migrations/001_init.sql
```

### 3. Push the repo to GitHub

```bash
cd fdi-agent-deploy
git init
git add .
git commit -m "Initial deploy"
gh repo create fdi-agent --public --source=. --push
# or: git remote add origin <repo-url> && git push -u origin main
```

### 4. Deploy to Streamlit Cloud

1. Go to https://share.streamlit.io
2. Sign in with GitHub
3. New app → pick the repo, branch = `main`, main file = `streamlit_app.py`
4. Advanced settings → Secrets — paste:

   ```toml
   DATABASE_URL = "postgresql+psycopg://user:pwd@ep-xxx.../neondb?sslmode=require"
   ANTHROPIC_API_KEY = "sk-ant-..."
   BRAVE_SEARCH_API_KEY = "..."
   ANTHROPIC_MODEL = "claude-sonnet-4-5"
   ```

5. Deploy. URL appears in ~3 min: `https://fdi-agent-yourname.streamlit.app`

### 5. Run your first ingestion

The cron job runs on a separate machine (Streamlit Cloud doesn't run cron).
Run from your laptop:

```bash
export DATABASE_URL='postgresql+psycopg://...'
python -m agent2_fdi_scraper.run --source fia --limit 10
```

Refresh the dashboard URL — companies appear.

### Caveats

- Streamlit Cloud apps sleep after 7 days of inactivity. They wake on access in ~30 sec.
- 1 GB RAM limit. Fine for the dashboard; not enough for the local-embedding backend. Use Voyage API instead.
- No background workers — cron has to run somewhere else (your laptop, a $5 VPS, Railway).

---

## Option B — Railway (single-host, includes cron + DB)

Railway runs Postgres + the Streamlit app + the cron job all on one platform.
Cost: ~$5/month for hobby use.

### 1. Create a Railway project

1. Go to https://railway.app
2. New project → Deploy from GitHub → pick this repo
3. Add Postgres plugin (creates DATABASE_URL env var automatically)

### 2. Configure two services

**Web service (the dashboard):**

- Build: `pip install -r requirements.txt`
- Start: `streamlit run streamlit_app.py --server.port=$PORT --server.address=0.0.0.0`
- Add env vars: `ANTHROPIC_API_KEY`, `BRAVE_SEARCH_API_KEY`

**Cron service (the daily orchestrator):**

- Build: `pip install -r requirements.txt`
- Cron schedule: `0 2 * * *` (Railway has built-in cron)
- Command: `python -m orchestrator.daily_run`
- Same env vars as above

### 3. Run migrations

Railway gives you a connection string in the dashboard; run migration as in Option A step 2.

### 4. Custom domain (optional)

Railway → Service → Settings → Custom Domain → add `fdi.yourdomain.com`,
update your DNS to the CNAME they provide.

---

## Option C — Tailscale + own machine (private link, $0)

Best if **only you** need access and you have a server already (NAS, home
desktop, $4 Hetzner VPS, etc.).

```bash
# On the server
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Run the dashboard
streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=8501
```

Tailscale gives the machine a stable hostname like `myserver.tailnet-xxxx.ts.net`.
On your phone/laptop with Tailscale installed, browse to:

```
http://myserver.tailnet-xxxx.ts.net:8501
```

To make it survive reboots, use the systemd unit in
`orchestrator/cron/systemd.sample` and add a separate one for the dashboard:

```ini
# /etc/systemd/system/fdi-dashboard.service
[Unit]
Description=FDI Agent dashboard
After=network-online.target

[Service]
User=fdi-agent
WorkingDirectory=/opt/fdi-agent
EnvironmentFile=/opt/fdi-agent/.env
ExecStart=/opt/fdi-agent/.venv/bin/streamlit run streamlit_app.py \
  --server.address=0.0.0.0 --server.port=8501 --server.headless=true
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now fdi-dashboard`.

---

## Setting up the daily cron

The cron job is **separate from the dashboard** — they don't have to live on the
same machine. Pick where based on your hosting choice:

- **Streamlit Cloud + Neon:** run cron on a $4 VPS, your laptop, or any server with `cron`
- **Railway:** use Railway's built-in cron service (above)
- **Self-hosted:** systemd timer (`orchestrator/cron/systemd.sample`) or crontab (`orchestrator/cron/crontab.sample`)

### Daily-target sanity check

The orchestrator defaults to **50 new companies/day**. This is realistic ONLY in
**backfill mode** — i.e. while you're working through 5 years of historical FIA
bulletins. After ~1500 companies (configurable via `BACKFILL_THRESHOLD`), it
auto-switches to **steady-state mode**, where:

- New-company target drops to ~5/day (matching real FDI license arrival rate)
- Re-enrichment target picks up the slack: ~30 existing companies refreshed/day
- People-discovery target stays high: ~100 new IT contacts/day

Override with env vars:

```bash
COMPANIES_DAILY_TARGET=50      # new licensings target
REENRICH_DAILY_TARGET=30       # existing companies refreshed
PEOPLE_DAILY_TARGET=100        # IT decision-makers found
EMAIL_VERIFY_DAILY_TARGET=100  # SMTP verifications
BACKFILL_THRESHOLD=1500        # switch to steady-state above this count
```

### What "50 companies/day" actually looks like

Recent FIA bulletins typically list 200-400 newly-licensed projects per month.
Of those, after filtering for manufacturing + matching the ICP, roughly 60-100
end up qualifying — about **2-4/day in steady state**.

To hit 50/day you must be in backfill mode pulling old bulletins. After
~30-60 days of backfill, your DB is full and the meaningful daily yield
shifts to people discovery + re-enrichment.

The orchestrator will tell you which mode it's in via the `Daily runs`
page — green "success" status means the target was hit, yellow "partial"
means the source ran out of new material (which is the *right* outcome
once you've caught up).
