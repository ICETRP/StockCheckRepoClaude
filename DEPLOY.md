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

## Local use is unaffected

Nothing changes for running it locally — `DASHBOARD_PASSWORD` is unset by default,
so `python app.py` still opens straight to the dashboard with no login, exactly as
before. The login gate only activates where you explicitly set that environment
variable (i.e. on Render).
