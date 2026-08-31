"""
Claude Algo Dashboard - local web UI for running the screener/backtester scripts
(EarningBeatMovmtm.py, Swing.py, breakout_screener.py) against Yahoo, Twelve Data,
or Alpaca, viewing results in the browser, and keeping a history of past runs.

Run with:  python app.py   (from this webapp/ folder)
Then open: http://127.0.0.1:5055
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, render_template, session, redirect, url_for

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import requests
except ImportError:
    requests = None

try:
    import yfinance as yf
except ImportError:
    yf = None

BASE_DIR = Path(__file__).resolve().parent          # webapp/
ALGO_DIR = BASE_DIR.parent                            # Claude_Algo/
OUTPUTS_DIR = ALGO_DIR / "outputs"
LOGS_DIR = OUTPUTS_DIR / "logs"
MANIFEST_PATH = OUTPUTS_DIR / "runs_manifest.json"

OUTPUTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv(ALGO_DIR / ".env")
except ImportError:
    pass

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # always re-read index.html from disk, even with debug=False

# Password gate: OFF by default (local use, unchanged). Set DASHBOARD_PASSWORD in the
# environment (e.g. when deployed publicly) to require a login before anything else works.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")


@app.before_request
def _require_login():
    if not DASHBOARD_PASSWORD:
        return None  # auth disabled — local/default behavior
    if request.endpoint in ("login", "static"):
        return None
    if not session.get("authed"):
        return redirect(url_for("login", next=request.path))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if not DASHBOARD_PASSWORD:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), DASHBOARD_PASSWORD):
            session["authed"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^]{1,10}$")

SCRIPTS = {
    "earnings": {
        "label": "Earnings-Beat Momentum (PEAD)",
        "file": "EarningBeatMovmtm.py",
        "provider_flag": "--data-provider",
        "providers": ["yahoo", "twelvedata", "alpaca"],
        "default_watchlist": [
            "NVDA", "AMD", "AVGO", "MSFT", "AAPL", "GOOGL", "META",
            "AMZN", "CRM", "ORCL", "MU", "LRCX", "ANET", "PANW",
        ],
        "supports_universe": False,
        "fields": [
            {"name": "min_eps_surprise", "flag": "--min-eps-surprise", "label": "Min EPS Surprise %", "type": "number", "step": "0.1", "default": 5.0},
            {"name": "min_reaction", "flag": "--min-reaction", "label": "Min Next-Day Reaction %", "type": "number", "step": "0.1", "default": 2.0},
            {"name": "min_volume", "flag": "--min-volume", "label": "Min Avg Volume", "type": "number", "step": "100000", "default": 1000000},
            {"name": "max_hold_days", "flag": "--max-hold-days", "label": "Max Hold Days (backtest)", "type": "number", "step": "1", "default": 60},
        ],
    },
    "swing": {
        "label": "Swing RS Leaders",
        "file": "Swing.py",
        "provider_flag": "--data-provider",
        "providers": ["yahoo", "twelvedata", "alpaca"],
        "default_watchlist": [
            "NVDA", "AMD", "META", "AVGO", "MSFT", "AAPL", "GOOGL",
            "AMZN", "TSLA", "NFLX", "CRM", "ORCL",
        ],
        "supports_universe": False,
        "fields": [
            {"name": "benchmark", "flag": "--benchmark", "label": "Benchmark Symbol", "type": "text", "default": "SPY"},
            {"name": "min_volume", "flag": "--min-volume", "label": "Min Avg Volume", "type": "number", "step": "100000", "default": 1000000},
        ],
    },
    "breakout": {
        "label": "Breakout / Coil Screener",
        "file": "breakout_screener.py",
        "provider_flag": "--data-source",
        "providers": ["yahoo", "twelvedata", "alpaca"],
        "default_watchlist": [
            "NVDA", "AMD", "AVGO", "MSFT", "AAPL", "GOOGL", "META", "AMZN",
            "PLTR", "SMCI", "CRWD", "ANET", "MU", "TSLA",
        ],
        "supports_universe": True,
        "universe_options": ["penny", "midcap", "bluechip"],
        "entry_modes": ["breakout", "setup"],
        "fields": [
            {"name": "resistance_window", "flag": "--resistance-window", "label": "Resistance Window (days)", "type": "number", "step": "1", "default": 50},
            {"name": "near_resistance_pct", "flag": "--near-resistance-pct", "label": "Near-Resistance %", "type": "number", "step": "0.1", "default": 3.0},
            {"name": "min_volume", "flag": "--min-volume", "label": "Min Avg Volume", "type": "number", "step": "100000", "default": 1000000},
            {"name": "universe_limit", "flag": "--universe-limit", "label": "Universe Limit", "type": "number", "step": "10", "default": 100},
        ],
    },
}

JOBS = {}
JOBS_LOCK = threading.Lock()


def _clean_tickers(raw):
    if not raw:
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,\s]+", raw.strip())
    else:
        parts = raw
    out = []
    for p in parts:
        p = p.strip().upper()
        if p and TICKER_RE.match(p):
            out.append(p)
    return out


def _load_manifest():
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _append_manifest(entry):
    with JOBS_LOCK:
        data = _load_manifest()
        data.append(entry)
        MANIFEST_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_csv_table(csv_path: Path, limit=500):
    if pd is None or not csv_path.exists():
        return None
    try:
        df = pd.read_csv(csv_path)
        df = df.fillna("")
        if len(df) > limit:
            df = df.head(limit)
        return {"columns": list(df.columns), "rows": df.to_dict(orient="records")}
    except Exception as exc:
        return {"error": str(exc)}


def _build_command(script_key, params):
    cfg = SCRIPTS[script_key]
    py = sys.executable
    cmd = [py, cfg["file"]]

    universe = params.get("universe") or ""
    if cfg.get("supports_universe") and universe:
        cmd += ["--universe", universe]
        limit = params.get("universe_limit")
        if limit:
            cmd += ["--universe-limit", str(int(limit))]
    else:
        tickers = _clean_tickers(params.get("tickers"))
        if not tickers:
            tickers = cfg["default_watchlist"]
        cmd += ["--tickers"] + tickers

    provider = params.get("provider")
    if provider and provider in cfg["providers"]:
        cmd += [cfg["provider_flag"], provider]

    if cfg.get("entry_modes") and params.get("entry_mode") in cfg["entry_modes"]:
        cmd += ["--entry-mode", params["entry_mode"]]

    for field in cfg["fields"]:
        # universe_limit already handled above for breakout
        if field["name"] == "universe_limit":
            continue
        val = params.get(field["name"])
        if val in (None, ""):
            continue
        cmd += [field["flag"], str(val)]

    backtest = bool(params.get("backtest"))
    if backtest:
        cmd += ["--backtest"]
        start = params.get("start") or "2023-01-01"
        cmd += ["--start", start]
        end = params.get("end")
        if end:
            cmd += ["--end", end]

    return cmd, backtest


def _run_job(job_id, cmd, csv_path):
    job = JOBS[job_id]
    job["status"] = "running"
    log_path = LOGS_DIR / f"{job_id}.log"
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(ALGO_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, universal_newlines=True,
        )
        job["proc"] = proc
        with open(log_path, "w", encoding="utf-8") as lf:
            for line in proc.stdout:
                line = line.rstrip("\n")
                job["log"].append(line)
                lf.write(line + "\n")
                lf.flush()
        proc.wait()
        job["returncode"] = proc.returncode
        job["status"] = "done" if proc.returncode == 0 else "error"
    except Exception as exc:
        job["log"].append(f"[webapp] failed to launch: {exc}")
        job["status"] = "error"
        job["returncode"] = -1
    job["finished"] = datetime.now().isoformat(timespec="seconds")

    entry = {
        "id": job_id,
        "script": job["script"],
        "label": SCRIPTS[job["script"]]["label"],
        "mode": job["mode"],
        "provider": job["provider"],
        "tickers": job["tickers_display"],
        "started": job["started"],
        "finished": job["finished"],
        "returncode": job["returncode"],
        "status": job["status"],
        "csv": str(csv_path.relative_to(ALGO_DIR)) if csv_path else None,
        "log": str(log_path.relative_to(ALGO_DIR)),
        "cmd": cmd,
    }
    _append_manifest(entry)


# ==================== CHART DATA (candles + indicators for the click-through view) ====================

CHART_LOOKBACK_DAYS = 420   # calendar days of history to fetch (warm-up for EMA50/BB/ATR + display window)
CHART_DISPLAY_BARS = 160    # trading days actually sent to the frontend
FIB_WINDOW = 90             # trading days used to find the swing high/low for Fibonacci levels
ATR_LEN = 14
ATR_MULT = 2.5              # stop-loss distance in ATRs (matches breakout_screener.py's convention)
PROJECTION_DAYS = 21        # ~1 trading month


def _chart_fetch_yahoo(ticker, start, end):
    if yf is None:
        raise ValueError("yfinance is not installed")
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No price data returned for {ticker} (Yahoo).")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close", "volume"]].dropna()


def _chart_fetch_twelvedata(ticker, start, end):
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        raise ValueError("TWELVEDATA_API_KEY not set in .env")
    url = ("https://api.twelvedata.com/time_series"
           f"?symbol={ticker}&interval=1day&start_date={start}&end_date={end}&outputsize=5000&format=JSON&apikey={key}")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "values" not in data:
        raise ValueError(f"No data from Twelve Data for {ticker}: {data.get('message', data)}")
    df = pd.DataFrame(data["values"])
    df["date"] = pd.to_datetime(df["datetime"])
    df = df.set_index("date").sort_index()
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    return df[["open", "high", "low", "close", "volume"]]


def _chart_fetch_alpaca(ticker, start, end):
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise ValueError("ALPACA_API_KEY / ALPACA_API_SECRET not set in .env")
    # market data always lives on data.alpaca.markets, regardless of paper/live trading base URL.
    # Free/paper accounts are only entitled to the IEX feed, not the default SIP feed - requesting
    # SIP on a free key returns 403 Forbidden, so pin feed=iex unless the account says otherwise.
    url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
    feed = os.environ.get("ALPACA_DATA_FEED", "iex")
    params = {"timeframe": "1Day", "start": start, "end": end, "limit": 10000, "adjustment": "raw", "feed": feed}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    if resp.status_code == 403:
        raise ValueError(
            "Alpaca returned 403 Forbidden. Your account isn't entitled to the requested feed "
            f"('{feed}'). Free/paper accounts should use the IEX feed (default here) - if this "
            "still fails, verify ALPACA_API_KEY/ALPACA_API_SECRET are correct market-data keys."
        )
    resp.raise_for_status()
    data = resp.json()
    bars = data.get("bars") or []
    if not bars:
        raise ValueError(f"No bars returned from Alpaca for {ticker}")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"])
    df = df.set_index("t").sort_index()
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["open", "high", "low", "close", "volume"]]


CHART_PROVIDERS = {
    "yahoo": _chart_fetch_yahoo,
    "twelvedata": _chart_fetch_twelvedata,
    "alpaca": _chart_fetch_alpaca,
}


def _series_points(df, col):
    return [{"time": idx.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
            for idx, v in df[col].items() if pd.notna(v)]


def _compute_chart_payload(ticker, provider):
    fetch = CHART_PROVIDERS[provider]
    end = datetime.today()
    start = end - timedelta(days=CHART_LOOKBACK_DAYS)
    df = fetch(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if len(df) < 30:
        raise ValueError(f"Not enough price history returned for {ticker} to build a chart.")

    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std

    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(ATR_LEN).mean()
    df["resistance"] = high.shift(1).rolling(50).max()

    df = df.dropna(subset=["bb_upper", "atr"])
    if df.empty:
        raise ValueError(f"Not enough price history returned for {ticker} to compute indicators.")

    disp = df.tail(CHART_DISPLAY_BARS)

    fib_src = df.tail(FIB_WINDOW)
    swing_high = float(fib_src["high"].max())
    swing_low = float(fib_src["low"].min())
    uptrend = fib_src["high"].values.argmax() > fib_src["low"].values.argmin()
    levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    fib_levels = []
    for lv in levels:
        price = (swing_high - (swing_high - swing_low) * lv) if uptrend else (swing_low + (swing_high - swing_low) * lv)
        fib_levels.append({"level": lv, "price": round(price, 2)})

    last = disp.iloc[-1]
    last_close = float(last["close"])
    last_atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
    sl = round(last_close - ATR_MULT * last_atr, 2)
    risk = max(last_close - sl, 0.01)
    tp = round(last_close + 2 * risk, 2)
    resistance = round(float(last["resistance"]), 2) if pd.notna(last["resistance"]) else None

    # 1-month-ahead projection: trend drift from the recent EMA20 slope, plus an
    # ATR-based volatility cone (spread grows with sqrt(time), like a random walk).
    ema20_series = disp["ema20"]
    drift_per_day = float((ema20_series.iloc[-1] - ema20_series.iloc[-6]) / 5) if len(ema20_series) >= 6 else 0.0
    future_dates = pd.bdate_range(disp.index[-1] + pd.Timedelta(days=1), periods=PROJECTION_DAYS)
    projection = []
    for h, dt in enumerate(future_dates, start=1):
        mid = last_close + drift_per_day * h
        spread = last_atr * (h ** 0.5)
        projection.append({
            "time": dt.strftime("%Y-%m-%d"),
            "mid": round(mid, 2),
            "upper": round(mid + spread, 2),
            "lower": round(mid - spread, 2),
        })

    candles = [{
        "time": idx.strftime("%Y-%m-%d"),
        "open": round(float(row["open"]), 2),
        "high": round(float(row["high"]), 2),
        "low": round(float(row["low"]), 2),
        "close": round(float(row["close"]), 2),
        "volume": float(row["volume"]),
    } for idx, row in disp.iterrows()]

    verdict = _compute_verdict(disp, last_close, resistance)

    return {
        "ticker": ticker,
        "provider": provider,
        "candles": candles,
        "ema20": _series_points(disp, "ema20"),
        "ema50": _series_points(disp, "ema50"),
        "bb_upper": _series_points(disp, "bb_upper"),
        "bb_mid": _series_points(disp, "bb_mid"),
        "bb_lower": _series_points(disp, "bb_lower"),
        "fib_levels": fib_levels,
        "fib_direction": "up" if uptrend else "down",
        "sl": sl,
        "tp": tp,
        "resistance": resistance,
        "last_close": round(last_close, 2),
        "atr": round(last_atr, 2),
        "projection": projection,
        "verdict": verdict,
    }


def _compute_verdict(disp, last_close, resistance):
    """Simple, explainable rule-based read: trend + momentum + proximity-to-resistance.
    This is a mechanical summary of the indicators already on the chart, not investment advice."""
    score = 0
    notes = []

    ema20_last = float(disp["ema20"].iloc[-1])
    ema50_last = float(disp["ema50"].iloc[-1])
    if last_close > ema20_last > ema50_last:
        score += 1
        notes.append("Uptrend: price is above both EMA20 and EMA50.")
    elif last_close < ema20_last < ema50_last:
        score -= 1
        notes.append("Downtrend: price is below both EMA20 and EMA50.")
    else:
        notes.append("No clear trend: EMA20/EMA50 are mixed.")

    if len(disp) > 10:
        close_10d_ago = float(disp["close"].iloc[-11])
        momentum_pct = (last_close / close_10d_ago - 1) * 100
        if momentum_pct > 3:
            score += 1
            notes.append(f"Positive momentum: up {momentum_pct:.1f}% over the last 10 sessions.")
        elif momentum_pct < -3:
            score -= 1
            notes.append(f"Negative momentum: down {abs(momentum_pct):.1f}% over the last 10 sessions.")
        else:
            notes.append(f"Flat momentum: {momentum_pct:+.1f}% over the last 10 sessions.")

    bb_upper_last = float(disp["bb_upper"].iloc[-1])
    bb_lower_last = float(disp["bb_lower"].iloc[-1])
    if last_close >= bb_upper_last:
        notes.append("Price is at/above the upper Bollinger Band - extended, chasing here is higher risk.")
    elif last_close <= bb_lower_last:
        notes.append("Price is at/below the lower Bollinger Band - stretched to the downside.")

    if resistance:
        dist_pct = (resistance - last_close) / resistance * 100
        if 0 <= dist_pct <= 5:
            score += 1
            notes.append(f"Within {dist_pct:.1f}% of resistance (${resistance}) - watch for a breakout.")
        elif dist_pct < 0:
            notes.append(f"Already trading above its recent resistance (${resistance}).")

    if score >= 2:
        action = "BUY"
    elif score <= -2:
        action = "SELL"
    else:
        action = "HOLD"

    return {"action": action, "score": score, "notes": notes}


@app.route("/api/chart/<ticker>")
def api_chart(ticker):
    provider = (request.args.get("provider") or "yahoo").lower()
    if provider not in CHART_PROVIDERS:
        return jsonify({"error": f"unknown provider '{provider}'"}), 400
    ticker = ticker.strip().upper()
    if not TICKER_RE.match(ticker):
        return jsonify({"error": "invalid ticker"}), 400
    try:
        return jsonify(_compute_chart_payload(ticker, provider))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scripts")
def api_scripts():
    return jsonify(SCRIPTS)


@app.route("/api/run", methods=["POST"])
def api_run():
    body = request.get_json(force=True, silent=True) or {}
    script_key = body.get("script")
    if script_key not in SCRIPTS:
        return jsonify({"error": "unknown script"}), 400

    cmd, backtest = _build_command(script_key, body)

    job_id = uuid.uuid4().hex[:12]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "backtest" if backtest else "screener"
    csv_name = f"{script_key}_{mode}_{ts}_{job_id}.csv"
    csv_path = OUTPUTS_DIR / csv_name
    cmd += ["--csv", str(Path("outputs") / csv_name)]

    tickers_display = body.get("universe") or ", ".join(_clean_tickers(body.get("tickers"))) or "default watchlist"

    JOBS[job_id] = {
        "id": job_id,
        "script": script_key,
        "mode": mode,
        "provider": body.get("provider"),
        "tickers_display": tickers_display,
        "status": "queued",
        "log": [],
        "started": datetime.now().isoformat(timespec="seconds"),
        "finished": None,
        "returncode": None,
        "cmd": cmd,
    }

    thread = threading.Thread(target=_run_job, args=(job_id, cmd, csv_path), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "cmd": cmd})


@app.route("/api/jobs/<job_id>")
def api_job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    since = int(request.args.get("since", 0))
    log_slice = job["log"][since:]
    resp = {
        "id": job_id,
        "status": job["status"],
        "log": log_slice,
        "total": len(job["log"]),
        "returncode": job["returncode"],
        "started": job["started"],
        "finished": job["finished"],
        "cmd": job["cmd"],
        "provider": job.get("provider"),
    }
    if job["status"] in ("done", "error"):
        ts_matches = [f for f in OUTPUTS_DIR.glob(f"*_{job_id}.csv")]
        if ts_matches:
            resp["table"] = _read_csv_table(ts_matches[0])
            resp["csv"] = str(ts_matches[0].relative_to(ALGO_DIR))
    return jsonify(resp)


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    proc = job.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()
        job["log"].append("[webapp] cancelled by user")
        job["status"] = "error"
        job["returncode"] = -1
        job["finished"] = datetime.now().isoformat(timespec="seconds")
    return jsonify({"ok": True})


@app.route("/api/history")
def api_history():
    data = _load_manifest()
    data = list(reversed(data))[:100]
    return jsonify(data)


@app.route("/api/history/<job_id>/data")
def api_history_data(job_id):
    data = _load_manifest()
    entry = next((e for e in data if e["id"] == job_id), None)
    if not entry:
        return jsonify({"error": "not found"}), 404
    resp = dict(entry)
    if entry.get("csv"):
        resp["table"] = _read_csv_table(ALGO_DIR / entry["csv"])
    log_path = ALGO_DIR / entry["log"]
    if log_path.exists():
        resp["full_log"] = log_path.read_text(encoding="utf-8").splitlines()
    return jsonify(resp)


if __name__ == "__main__":
    # Cloud hosts (Render, Railway, Fly, ...) inject PORT and expect a 0.0.0.0 bind.
    # Locally, with no PORT set, keep the original 127.0.0.1:5055 behavior unchanged.
    port = int(os.environ.get("PORT", 5055))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    if DASHBOARD_PASSWORD is None and host == "0.0.0.0":
        print("WARNING: bound to 0.0.0.0 with no DASHBOARD_PASSWORD set — the dashboard is unauthenticated and, "
              "if this host is publicly reachable, open to anyone. Set DASHBOARD_PASSWORD before exposing it.")
    print(f"Claude Algo Dashboard running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
