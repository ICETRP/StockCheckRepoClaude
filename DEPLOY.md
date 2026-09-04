# Deploying Claude Algo Dashboard to the web (free)

This lets you open the dashboard from any device via a URL, with no need for your
own PC to be running. Recommended host: **Render.com** — free web service tier,
supports Docker, background subprocess execution, and outbound API calls (unlike
some free hosts that block outbound network access, which this app needs for
Yahoo/Twelve Data/Alpaca).

Free-tier tradeoffs to know upfront:
- The instance **spins down after ~15 minutes idle** and takes ~30-60s to wake up
  on your next visit. Fine for personal, occasional use.
- The disk is **ephemeral** — run history/CSVs may be wiped on redeploy or restart.
  Treat the web version as a live dashboard, not permanent storage.
- The app is now reachable by anyone with the link, so a password gate is required
  (added — see below). **Use a strong, unique password**, since there's no
  rate-limiting on login attempts.

## 1. Put the code on GitHub (private repo recommended)

From `D:\TestProj\Claude_Algo`:
```bash
git init
git add .
git commit -m "Claude Algo dashboard"
```
Then create a new **private** repository on github.com (don't add a README there),
and push:
```bash
git remote add origin https://github.com/<you>/claude-algo.git
git branch -M main
git push -u origin main
```
`.env` is excluded via `.gitignore` — your API keys never leave your machine this way.

## 2. Create a Render account and deploy

1. Go to render.com and sign up (free, can use your GitHub login).
2. **New +** → **Web Service** → connect your `claude-algo` repo.
3. Render will detect the `Dockerfile` at the repo root automatically. If asked:
   - **Environment**: Docker
   - **Instance Type**: Free
4. Under **Environment Variables**, add (values from your local `.env`):
   - `TWELVEDATA_API_KEY`
   - `ALPACA_API_KEY`
   - `ALPACA_API_SECRET`
   - `ALPACA_BASE_URL` = `https://paper-api.alpaca.markets`
   - `DASHBOARD_PASSWORD` = a strong password you choose (this turns on the login screen)
   - `FLASK_SECRET_KEY` = any long random string (e.g. generate one with
     `python -c "import secrets; print(secrets.token_hex(32))"`)
5. Click **Create Web Service**. First build takes a few minutes.
6. Once live, Render gives you a URL like `https://claude-algo-xxxx.onrender.com`.
   Open it, enter your `DASHBOARD_PASSWORD`, and you're in.

## 3. Using it day to day

- Bookmark the Render URL on your phone/laptop.
- If it's been idle, the first request after a while just takes ~30-60s to wake up.
- To update the deployed app after making local changes: commit and
  `git push` — Render auto-redeploys on push.

## 4. Trade Log setup (one-time, so trades survive future redeploys)

The dashboard now has a **Trade Log** page (`/trades`) for recording entries, stop-losses,
and exits. It's backed by a Google Sheet instead of a local file, since Render's disk is
wiped on redeploy (see the tradeoffs note above) — a Google Sheet persists independently
and you can also view/edit it directly from your phone.

**a. Create the Google service account (one-time, ~5 min):**
1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a project
   (or reuse one).
2. Enable the **Google Sheets API** for that project (APIs & Services → Enable APIs →
   search "Google Sheets API" → Enable).
3. Go to **APIs & Services → Credentials → Create Credentials → Service Account**. Give
   it any name (e.g. `trade-log-writer`). No roles needed.
4. Open the new service account → **Keys** tab → **Add Key → Create new key → JSON**.
   This downloads a `.json` key file — keep it private, never commit it to git.
5. Note the service account's email address (looks like
   `trade-log-writer@your-project.iam.gserviceaccount.com`).

**b. Create the sheet:**
1. Create a new Google Sheet (any name, e.g. "Claude Algo Trade Log").
2. Click **Share** and share it with the service account's email from step a.5, with
   **Editor** access.
3. Copy the sheet's ID from its URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_PART_IS_THE_ID`**`/edit`.

**c. Set the environment variables** (both locally in `.env` and on Render, under
**Environment**):
- `TRADE_LOG_SHEET_ID` = the sheet ID from step b.3 (or paste the full URL — either works).
- `GOOGLE_SERVICE_ACCOUNT_JSON` = the **entire contents** of the downloaded key file,
  minified to a single line. Minify it with:
  ```bash
  python -c "import json;print(json.dumps(json.load(open('path/to/key.json'))))"
  ```
  Paste that one-line output as the value. Locally in `.env`, wrap it in single quotes:
  `GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account", ...}'`

Once both variables are set, `git push` and Render's auto-redeploy picks it up — no
other deploy step needed. The sheet gets a header row automatically on first use, and
every trade you log or close writes straight to it.

## Local use is unaffected

Nothing changes for running it locally — `DASHBOARD_PASSWORD` is unset by default,
so `python app.py` still opens straight to the dashboard with no login, exactly as
before. The login gate only activates where you explicitly set that environment
variable (i.e. on Render).
