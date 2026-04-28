#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — push to GitHub + run schema migration in one go.
#
# Run from your laptop after extracting the lead-gen-agent bundle.
#
# Prerequisites (install if missing):
#   - git              brew install git  /  apt install git
#   - psql             brew install libpq && brew link --force libpq
#                      apt install postgresql-client
#   - npx (Node 18+)   nvm install --lts  /  apt install nodejs npm
#
# Usage:
#   ./bootstrap.sh
# =============================================================================

set -euo pipefail

REPO_URL="https://github.com/Vmcuong23/Lead-gen-agent.git"
REPO_DIR="$(pwd)"

# Colors for output
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red() { printf "\033[31m%s\033[0m\n" "$*"; }

# ---------------------------------------------------------------------------
# 1. Verify we're in the right place
# ---------------------------------------------------------------------------
if [ ! -f "streamlit_app.py" ] || [ ! -d "agent1_schema" ]; then
  red "ERROR: Run this script from inside the lead-gen-agent directory."
  red "Expected to find streamlit_app.py and agent1_schema/ here."
  exit 1
fi
green "✓ Found project files"

# ---------------------------------------------------------------------------
# 2. Set up Neon database (interactive)
# ---------------------------------------------------------------------------
if [ -z "${DATABASE_URL:-}" ]; then
  yellow "DATABASE_URL is not set. Let's create one with Neon."
  yellow "(You'll be prompted to log in via browser if it's your first time.)"
  echo
  read -p "Press Enter to continue, or Ctrl+C to abort if you already have a Postgres URL..."

  npx neonctl@latest auth || true
  echo
  yellow "Creating a Neon project for lead-gen-agent..."
  echo "If you already have one, skip this step (Ctrl+C) and just paste its connection string below."
  echo
  if npx neonctl@latest projects create --name lead-gen-agent 2>&1 | tee /tmp/neon-out.log; then
    yellow "Fetching connection string..."
    RAW_URL="$(npx neonctl@latest connection-string --project-id "$(npx neonctl@latest projects list --output json | python3 -c "import json,sys; data=json.load(sys.stdin); print([p['id'] for p in data['projects'] if p['name']=='lead-gen-agent'][0])")" 2>/dev/null || true)"
  else
    RAW_URL=""
  fi

  if [ -z "$RAW_URL" ]; then
    yellow "Couldn't auto-fetch connection string. Paste yours manually:"
    yellow "(get it from: https://console.neon.tech → your project → Connection Details)"
    read -p "DATABASE_URL: " RAW_URL
  fi

  # Convert postgres:// to postgresql+psycopg:// for SQLAlchemy
  if [[ "$RAW_URL" == postgres://* ]]; then
    RAW_URL="${RAW_URL/postgres:\/\//postgresql+psycopg:\/\/}"
  elif [[ "$RAW_URL" == postgresql://* && "$RAW_URL" != postgresql+psycopg://* ]]; then
    RAW_URL="${RAW_URL/postgresql:\/\//postgresql+psycopg:\/\/}"
  fi

  export DATABASE_URL="$RAW_URL"
  green "✓ DATABASE_URL configured"
fi

# Strip +psycopg for psql
PSQL_URL="${DATABASE_URL/+psycopg/}"

# ---------------------------------------------------------------------------
# 3. Run schema migration
# ---------------------------------------------------------------------------
yellow "Running database migration..."
psql "$PSQL_URL" -v ON_ERROR_STOP=1 -f agent1_schema/migrations/001_init.sql > /tmp/migration.log 2>&1 \
  && green "✓ Schema applied" \
  || { red "Migration failed. Check /tmp/migration.log"; tail -20 /tmp/migration.log; exit 1; }

# Verify by counting tables
TABLE_COUNT=$(psql "$PSQL_URL" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';")
green "✓ Database has $TABLE_COUNT tables"

# ---------------------------------------------------------------------------
# 4. Initialize git and push to your repo
# ---------------------------------------------------------------------------
if [ ! -d ".git" ]; then
  yellow "Initializing git repo..."
  git init -b main
  git config user.name "$(git config --global user.name || echo 'Vmcuong23')"
  git config user.email "$(git config --global user.email || echo 'noreply@github.com')"
fi

# Add the remote if not already added
if ! git remote get-url origin > /dev/null 2>&1; then
  git remote add origin "$REPO_URL"
  green "✓ Remote 'origin' added"
else
  CURRENT_REMOTE=$(git remote get-url origin)
  if [ "$CURRENT_REMOTE" != "$REPO_URL" ]; then
    yellow "Updating origin URL: $CURRENT_REMOTE → $REPO_URL"
    git remote set-url origin "$REPO_URL"
  fi
fi

# Stage everything
git add .

# Commit (allow empty in case nothing changed)
if git diff --cached --quiet; then
  yellow "No changes to commit — repo already up to date locally."
else
  git commit -m "Initial commit: FDI Agent for Vietnam IT decision-makers

- Agent 1: shared schema + SQLAlchemy models (Postgres + pgvector)
- Agent 2: FDI scraper (FIA bulletins + industrial-park tenants via Claude)
- Agent 3: people discovery (SERP + company sites + email verification)
- ICP scorer with YAML config
- Streamlit dashboard
- GitHub Actions workflow for daily cron"
  green "✓ Committed"
fi

# Push
yellow "Pushing to $REPO_URL..."
yellow "(GitHub will prompt for credentials. Use a Personal Access Token, not your password.)"
yellow "Create one at: https://github.com/settings/tokens — needs 'repo' scope."
echo
git push -u origin main \
  && green "✓ Pushed to GitHub" \
  || { red "Push failed. If the remote already has commits, try:"; \
       red "  git pull --rebase origin main && git push -u origin main"; exit 1; }

# ---------------------------------------------------------------------------
# 5. Print next steps
# ---------------------------------------------------------------------------
echo
green "======================================================================"
green "  ALL DONE — your repo is live."
green "======================================================================"
echo
yellow "Next steps (do these in your browser):"
echo
echo "  1. Add GitHub Actions secrets:"
echo "     https://github.com/Vmcuong23/Lead-gen-agent/settings/secrets/actions"
echo
echo "     New repository secret:  DATABASE_URL"
echo "       value: $DATABASE_URL"
echo
echo "     New repository secret:  ANTHROPIC_API_KEY"
echo "       value: sk-ant-... (from https://console.anthropic.com)"
echo
echo "     New repository secret:  BRAVE_SEARCH_API_KEY"
echo "       value: ...           (from https://brave.com/search/api/)"
echo
echo "  2. Deploy to Streamlit Cloud:"
echo "     https://share.streamlit.io"
echo
echo "     Repository:   Vmcuong23/Lead-gen-agent"
echo "     Branch:       main"
echo "     Main file:    streamlit_app.py"
echo "     App URL:      https://lead-gen-agent.streamlit.app  (or pick your own)"
echo
echo "     Secrets (paste into Advanced → Secrets):"
cat <<EOF
       DATABASE_URL = "$DATABASE_URL"
       ANTHROPIC_API_KEY = "sk-ant-..."
       BRAVE_SEARCH_API_KEY = "..."
EOF
echo
echo "  3. Trigger first orchestrator run manually:"
echo "     https://github.com/Vmcuong23/Lead-gen-agent/actions/workflows/daily-orchestrator.yml"
echo "     Click 'Run workflow' → pick 'backfill' mode"
echo
echo "  4. After ~10 min, your dashboard will have data."
echo
green "======================================================================"
