# Claude_Algo — Commands & Usage

This folder contains three trading tools: `EarningBeatMovmtm.py` (Earnings-Beat Momentum screener & backtester), `Swing.py` (RS Swing Leaders screener & backtester), and `breakout_screener.py` (Breakout/coil screener & backtester).

## Web Dashboard (easiest way to run everything)

A local web UI lives in `webapp/` and wraps all three scripts — pick a strategy, a data provider (Yahoo / Twelve Data / Alpaca), and tickers from searchable dropdowns, hit Run, and watch live output + a results table in the browser. Every run is auto-saved to `outputs/` (CSV + log) and listed in a Run History panel you can click back into later.

**To start it:** double-click `run_webapp.bat` in this folder (installs dependencies on first run, then opens http://127.0.0.1:5055 in your browser).

Or from a terminal:

```bash
cd webapp
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5055. This is a local-only server (binds to 127.0.0.1) — nothing is exposed to the network. It reuses the same `.env` in this folder for API keys, so no extra setup is needed if you've already configured `TWELVEDATA_API_KEY` / `ALPACA_API_KEY` / `ALPACA_API_SECRET`.

---

## Command-line usage (advanced / scripting)

**What Claude_Algo Does**
- **Purpose:** lightweight research tools to screen for entry candidates and run simple backtests using daily OHLCV data.
- **Two strategies included:** `EarningBeatMovmtm.py` (post-earnings momentum / PEAD) and `Swing.py` (Relative Strength swing leader strategy).
- **Data sources:** price bars can come from Yahoo (`yfinance`), Twelve Data, or Alpaca (selectable via `DATA_PROVIDER` in `.env` or `--data-provider` CLI). Earnings/metadata are fetched via `yfinance` by default.
- **Outputs:** CSV results for screeners/backtests, per-run logs, and (via `run_backtests.py`) per-provider HTML reports plus a combined HTML report.
- **How to run:** use the scripts in this folder. Use `.env` to store API keys (`TWELVEDATA_API_KEY`, `ALPACA_API_KEY`, `ALPACA_API_SECRET`) so you don't need to pass them on the command line.

**Prerequisites**
- Python 3.8 or newer
- Install required packages:

```bash
pip install yfinance pandas numpy

# Optional: Twelve Data support
- If you prefer to use Twelve Data instead of Yahoo Finance, set `DATA_PROVIDER` and your API key in the `.env` file in this folder (example below).
- Install extra packages:

```bash
pip install requests python-dotenv
```

Example `.env` settings (stored in `Claude_Algo/.env`):

```
DATA_PROVIDER=twelvedata
TWELVEDATA_API_KEY=your_api_key_here
```

When `DATA_PROVIDER` is `twelvedata`, the scripts will use the Twelve Data time-series API for price data and fall back to `yfinance` for earnings metadata.

You can also select the provider at runtime using command-line flags:

```bash
# use Yahoo (default)
python EarningBeatMovmtm.py --data-provider yahoo

# use Twelve Data and pass API key on the command line
python EarningBeatMovmtm.py --data-provider twelvedata --td-api-key YOUR_KEY
```

The same flags are available for `Swing.py`.

Alpaca usage example (paper API):

```bash
# set provider and pass Alpaca keys on the command line
python EarningBeatMovmtm.py --data-provider alpaca --alpaca-key YOUR_KEY --alpaca-secret YOUR_SECRET --alpaca-base https://paper-api.alpaca.markets

# or set in .env
ALPACA_API_KEY=your_key_here
ALPACA_API_SECRET=your_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```
```

**Run from this folder**
Open a terminal in `d:\TestProj\Claude_Algo` and run the commands below.

**EarningBeatMovmtm.py (Earnings-Beat Momentum)**

- Run the screener (default watchlist):

```bash
python EarningBeatMovmtm.py
```

- Screen specific tickers:

```bash
python EarningBeatMovmtm.py --tickers NVDA AMD MSFT
```

- Run a backtest for one or more tickers:

```bash
python EarningBeatMovmtm.py --backtest --tickers NVDA --start 2022-01-01 --end 2023-01-01
```

- Save screener output to CSV:

```bash
python EarningBeatMovmtm.py --csv results.csv
```

- Important options:
  - `--min-eps-surprise` (float) — minimum EPS beat % (default 5.0)
  - `--min-reaction` (float) — minimum next-day price reaction % (default 2.0)
  - `--min-volume` (float) — minimum avg daily volume filter (default 1_000_000)
  - `--max-hold-days` (int) — max hold days for backtest (default 60)

**Swing.py (RS Swing Leaders)**

- Run the screener (default watchlist):

```bash
python Swing.py
```

- Screen specific tickers:

```bash
python Swing.py --tickers NVDA AMD META
```

- Run a backtest for a ticker:

```bash
python Swing.py --backtest --tickers NVDA --start 2023-01-01 --end 2023-12-31
```

- Save screener output to CSV:

```bash
python Swing.py --csv rs_results.csv
```

- Important options:
  - `--benchmark` (str) — benchmark symbol (default `SPY`)
  - `--min-volume` (float) — minimum avg daily volume (default 1_000_000)
  - `--backtest` — run backtest mode
  - `--start`, `--end` — backtest date range

**breakout_screener.py (Breakout / Coil Screener) — blind market-wide search**

Instead of a fixed watchlist, `--universe` blind-searches the whole market via Yahoo's screener API, filtered by market-cap bucket and minimum volume:

```bash
# penny stocks (price < $5) with at least 1M avg daily volume
python breakout_screener.py --universe penny --min-volume 1000000

# midcap ($2B-$10B market cap) with at least 5M avg daily volume
python breakout_screener.py --universe midcap --min-volume 5000000

# blue chip (>$10B market cap) with at least 10M avg daily volume
python breakout_screener.py --universe bluechip --min-volume 10000000

# limit how many tickers are pulled from the universe (default 100)
python breakout_screener.py --universe midcap --universe-limit 30
```

Results are sorted by volume (highest first) before screening, so raising `--min-volume` also biases the pulled tickers toward the most liquid names in that cap bucket. `--universe` replaces `--tickers`/the default watchlist for that run.

Additional usage notes
- Run the screener for specific tickers using Twelve Data as the price source:

```bash
python breakout_screener.py --tickers BTE PSEC GTM --data-source twelvedata
```

- Prerequisites for Twelve Data: set `TWELVEDATA_API_KEY` in your environment or a `.env` file. Example (PowerShell, session only):

```powershell
$env:TWELVEDATA_API_KEY = "YOUR_KEY"
# or persist for the current user
setx TWELVEDATA_API_KEY "YOUR_KEY"
```

- Or create a `.env` file in this folder with:

```
DATA_SOURCE=twelvedata
TWELVEDATA_API_KEY=your_api_key_here
```

- Install the optional extras used by the Twelve Data code path:

```bash
pip install requests python-dotenv
```

- Important: the `--universe` (blind-search) implementation currently uses Yahoo via `yfinance` (`yf.screen`) and does not use the `--data-source` flag. In other words, `--universe` pulls tickers via Yahoo regardless of `--data-source`. If you need a TwelveData-based market-wide universe scan, tell me and I can add a custom implementation (requires TwelveData account/rate-limit considerations).

- Note on flag names: some scripts in this folder use `--data-provider` while `breakout_screener.py` uses `--data-source` — both control which API provides OHLCV data; use the exact flag shown when running each script.

**Notes & troubleshooting**
- Some module docstrings refer to different example filenames (e.g., `rs_swing_leaders.py` or `earnings_beat_momentum.py`). Use the actual filenames present here: `EarningBeatMovmtm.py` and `Swing.py`.
- If you see an import error, install the dependencies listed above.
- To run on Windows PowerShell, you can prefix with `python .\EarningBeatMovmtm.py` or `python .\Swing.py`.

If you'd like, I can also:
- Add examples for scheduling these scripts (Task Scheduler / cron)
- Run a quick backtest and capture sample output

