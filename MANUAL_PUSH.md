# Manual push to GitHub

You've chosen to skip the bootstrap script and push manually. Here are
the exact commands.

## Prerequisites

- `git` installed and configured with your name/email
- A GitHub Personal Access Token (PAT) with `repo` scope:
  https://github.com/settings/tokens

## Steps

### 1. Extract the bundle

```bash
tar -xzf lead-gen-agent.tar.gz
cd lead-gen-agent
```

### 2. Initialize git

```bash
git init -b main
```

### 3. Add your repo as the remote

```bash
git remote add origin https://github.com/Vmcuong23/Lead-gen-agent.git
```

If you've already initialized the repo on GitHub with a README or other
files, fetch first to merge:

```bash
git pull --rebase origin main
```

### 4. Stage and commit everything

```bash
git add .
git commit -m "Initial commit: FDI Agent for Vietnam IT decision-makers"
```

### 5. Push

```bash
git push -u origin main
```

When prompted for credentials, use:
- Username: `Vmcuong23`
- Password: your **Personal Access Token** (not your GitHub password — GitHub
  removed password auth in 2021)

If you've configured GitHub CLI (`gh auth login`) or set up SSH, those work
too — `git push` will use whichever is set up.

## After the push

The repo is on GitHub. Now configure secrets and deploy:

1. **Add secrets** at https://github.com/Vmcuong23/Lead-gen-agent/settings/secrets/actions
   - `DATABASE_URL` (your Neon connection string, with `+psycopg`)
   - `ANTHROPIC_API_KEY`
   - `BRAVE_SEARCH_API_KEY`

2. **Apply the schema** to your Neon database (one-time):
   ```bash
   PSQL_URL="$(echo "$DATABASE_URL" | sed 's/+psycopg//')"
   psql "$PSQL_URL" -f agent1_schema/migrations/001_init.sql
   ```

3. **Deploy on Streamlit Cloud** at https://share.streamlit.io
   - Repository: `Vmcuong23/Lead-gen-agent`
   - Branch: `main`
   - Main file: `streamlit_app.py`
   - Paste the same three secrets into Advanced → Secrets

4. **Trigger first orchestrator run** at
   https://github.com/Vmcuong23/Lead-gen-agent/actions/workflows/daily-orchestrator.yml
   → Run workflow → mode `backfill`

Full step-by-step is in `README.md` and `DEPLOYMENT.md`.

## If something fails

Paste the error in chat. The most common ones:

- **`Permission denied (publickey)`** — you have an SSH remote but no SSH key
  configured. Switch to HTTPS:
  `git remote set-url origin https://github.com/Vmcuong23/Lead-gen-agent.git`

- **`Authentication failed`** — using your password instead of a PAT, or the
  PAT lacks `repo` scope. Generate a new one at
  https://github.com/settings/tokens.

- **`Updates were rejected because the remote contains work...`** — the
  GitHub repo already has commits. Run:
  `git pull --rebase origin main` then `git push`

- **`fatal: refusing to merge unrelated histories`** — same situation, force
  the merge:
  `git pull --rebase origin main --allow-unrelated-histories`
