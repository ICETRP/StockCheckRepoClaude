"""
Breakout Screener — Stocks Coiling Near Resistance, About to Break Higher
=============================================================================
Finds stocks that are NOT already breaking out, but showing the classic
pre-breakout signature:
  1. Price sitting close to its N-day high (near resistance)
  2. Volatility contraction ("squeeze") — recent trading range is tighter
     than its own historical average, meaning the stock is coiling
  3. Volume beginning to build (accumulation), without yet spiking on a
     breakout day
  4. Price above its trend MA (still in an uptrend context, not basing
     after a downtrend)

Also flags stocks that have ALREADY broken out today (closed above
resistance on strong volume) as a separate "BROKEN OUT" signal, in case
you want to catch the move itself rather than the setup before it.

Install requirements first:
    pip install yfinance pandas numpy

Usage:
    python breakout_screener.py
    python breakout_screener.py --tickers NVDA AMD PLTR SMCI --resistance-window 50
    python breakout_screener.py --backtest --tickers PLTR --start 2023-01-01

Optional data sources:
  - `yahoo` (default, uses `yfinance`)
  - `twelvedata` (requires `TWELVEDATA_API_KEY` in env)
  - `alpaca` (requires `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in env)

Install requirements first (optional extras for data providers):
    pip install yfinance pandas numpy requests
    # optional: pip install alpaca-trade-api
"""

import argparse
import sys
import os
import requests
try:
    # optional: load environment variables from a .env file if present
    from dotenv import load_dotenv
    # prefer a .env next to this script, else cwd
    _env_local = os.path.join(os.path.dirname(__file__), ".env")
    _env_cwd = os.path.join(os.getcwd(), ".env")
    if os.path.exists(_env_local):
        load_dotenv(_env_local)
    elif os.path.exists(_env_cwd):
        load_dotenv(_env_cwd)
except Exception:
    # python-dotenv not installed or other issue — proceed without failing
    pass
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("Missing dependency. Install it with:\n    pip install yfinance pandas numpy")
    sys.exit(1)


# --- Optional API clients: TwelveData and Alpaca use requests or their SDKs if available
try:
    import alpaca_trade_api as alpaca
except Exception:
    alpaca = None


# ==================== CONFIG ====================

@dataclass
class BreakoutConfig:
    resistance_window: int = 50        # N-day high used as the resistance level
    near_resistance_pct: float = 3.0   # price must be within this % of the N-day high
    squeeze_window: int = 20           # window for measuring recent volatility contraction
    squeeze_lookback: int = 100        # longer window to compare "recent" volatility against
    squeeze_percentile: float = 30.0   # recent volatility must be in the bottom N percentile
    trend_ma_len: int = 50
    vol_avg_len: int = 20
    min_avg_volume: float = 1_000_000
    volume_build_pct: float = 10.0     # recent avg volume must be at least this % above the longer avg (accumulation)
    breakout_volume_mult: float = 1.5  # today's volume vs avg volume, to flag an ALREADY-broken-out day
    atr_len: int = 14
    atr_mult: float = 2.5              # suggested stop distance if you enter a breakout


DEFAULT_WATCHLIST = [
    "NVDA", "AMD", "AVGO", "MSFT", "AAPL", "GOOGL", "META", "AMZN",
    "PLTR", "SMCI", "CRWD", "ANET", "MU", "TSLA",
]

# Global data source (set from CLI): 'yahoo' (default), 'twelvedata', 'alpaca'
DATA_SOURCE = "yahoo"

# Market-cap buckets for --universe mode (min, max) in USD; max=None means unbounded.
CAP_BUCKETS = {
    "penny": (None, None),      # penny stocks are defined by price, not market cap
    "midcap": (2_000_000_000, 10_000_000_000),
    "bluechip": (10_000_000_000, None),
}
PENNY_MAX_PRICE = 5.0


# ==================== UNIVERSE (BLIND SEARCH) ====================

def fetch_universe_tickers(cap_bucket: str, min_volume: float, limit: int = 100) -> list[str]:
    """
    Blind-search the whole market via Yahoo's screener API instead of a fixed
    watchlist. cap_bucket is one of "penny", "midcap", "bluechip".
    """
    clauses = [
        yf.EquityQuery("is-in", ["exchange", "NMS", "NYQ"]),
        yf.EquityQuery("gte", ["avgdailyvol3m", min_volume]),
    ]

    if cap_bucket == "penny":
        clauses.append(yf.EquityQuery("lt", ["intradayprice", PENNY_MAX_PRICE]))
    else:
        lo, hi = CAP_BUCKETS[cap_bucket]
        if lo is not None:
            clauses.append(yf.EquityQuery("gte", ["intradaymarketcap", lo]))
        if hi is not None:
            clauses.append(yf.EquityQuery("lt", ["intradaymarketcap", hi]))

    query = yf.EquityQuery("and", clauses)
    result = yf.screen(query, size=min(limit, 250), sortField="avgdailyvol3m", sortAsc=False)
    quotes = result.get("quotes", [])
    return [q["symbol"] for q in quotes if q.get("symbol")]


# ==================== DATA FETCHING ====================

def fetch_daily_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    # Dispatcher to multiple data providers
    src = DATA_SOURCE.lower() if DATA_SOURCE else "yahoo"
    if src == "yahoo":
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            raise ValueError(f"No price data returned for {ticker} (Yahoo).")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        return df[["open", "high", "low", "close", "volume"]].dropna()
    elif src == "twelvedata":
        return fetch_daily_data_twelvedata(ticker, start, end)
    elif src == "alpaca":
        return fetch_daily_data_alpaca(ticker, start, end)
    else:
        raise ValueError(f"Unknown data source: {DATA_SOURCE}")


def fetch_daily_data_twelvedata(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily OHLCV from TwelveData (https://twelvedata.com)
    Provide API key in env `TWELVEDATA_API_KEY`.
    """
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        raise ValueError("TWELVEDATA_API_KEY not set in environment")

    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol={ticker}&interval=1day&start_date={start}&end_date={end}&outputsize=5000&format=JSON&apikey={key}"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "values" not in data:
        raise ValueError(f"No data from TwelveData for {ticker}: {data}")
    rows = data["values"]
    df = pd.DataFrame(rows)
    df = df.rename(columns={"datetime": "date", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    return df[["open", "high", "low", "close", "volume"]]


def fetch_daily_data_alpaca(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily OHLCV from Alpaca Data API.
    Requires env vars: ALPACA_API_KEY, ALPACA_SECRET_KEY. Optional ALPACA_BASE_URL.
    """
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    base = os.environ.get("ALPACA_BASE_URL", "https://data.alpaca.markets/v2")
    if not (key and secret):
        raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment")

    # Prefer SDK if available
    if alpaca is not None:
        client = alpaca.REST(key, secret, base_url=os.environ.get("ALPACA_API_BASE", ""))
        # alpaca SDK uses get_bars; adapt depending on version
        try:
            bars = client.get_bars(ticker, alpaca.TimeFrame.Day, start=start, end=end).df
            bars.index = pd.to_datetime(bars.index)
            # columns may be ['open','high','low','close','volume'] already
            cols = [c for c in ["open", "high", "low", "close", "volume"] if c in bars.columns]
            return bars[cols]
        except Exception as e:
            # fall back to HTTP
            pass

    # HTTP fallback
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    url = f"{base}/stocks/{ticker}/bars?start={start}&end={end}&timeframe=1Day&limit=10000"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "bars" not in data:
        raise ValueError(f"No bars data from Alpaca for {ticker}: {data}")
    rows = data["bars"]
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"]) if "t" in df.columns else pd.to_datetime(df["timestamp"]) if "timestamp" in df.columns else None
    if df["t"].isnull().all():
        df.index = pd.to_datetime(df["time"]) if "time" in df.columns else pd.RangeIndex(len(df))
    else:
        df = df.set_index("t").sort_index()
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df[["open", "high", "low", "close", "volume"]]


# ==================== INDICATORS ====================

def compute_atr(df: pd.DataFrame, length: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def build_signal_frame(df: pd.DataFrame, cfg: BreakoutConfig) -> pd.DataFrame:
    df = df.copy()

    # --- Resistance level: rolling N-day high (excluding today, so it's a real prior level) ---
    df["resistance"] = df["high"].shift(1).rolling(cfg.resistance_window).max()
    df["pct_below_resistance"] = (df["resistance"] - df["close"]) / df["resistance"] * 100
    df["near_resistance"] = (df["pct_below_resistance"] >= 0) & \
                             (df["pct_below_resistance"] <= cfg.near_resistance_pct)

    # --- Volatility squeeze: recent daily range (as % of price) vs its own longer history ---
    daily_range_pct = (df["high"] - df["low"]) / df["close"] * 100
    df["recent_range_pct"] = daily_range_pct.rolling(cfg.squeeze_window).mean()
    df["range_percentile"] = df["recent_range_pct"].rolling(cfg.squeeze_lookback).rank(pct=True) * 100
    df["is_squeezing"] = df["range_percentile"] <= cfg.squeeze_percentile

    # --- Trend filter ---
    df["trend_ma"] = df["close"].rolling(cfg.trend_ma_len).mean()
    df["above_trend"] = df["close"] > df["trend_ma"]

    # --- Volume build-up (accumulation): short avg volume rising vs longer avg ---
    df["avg_volume_short"] = df["volume"].rolling(cfg.vol_avg_len).mean()
    df["avg_volume_long"] = df["volume"].rolling(cfg.vol_avg_len * 3).mean()
    df["volume_building"] = (df["avg_volume_short"] / df["avg_volume_long"] - 1) * 100 >= cfg.volume_build_pct
    df["liquid_enough"] = df["avg_volume_short"] >= cfg.min_avg_volume

    # --- ALREADY broke out today: closed above resistance on a volume spike ---
    df["broke_out_today"] = (df["close"] > df["resistance"]) & \
                             (df["volume"] >= cfg.breakout_volume_mult * df["avg_volume_short"])

    # --- Pre-breakout setup (the "about to break higher" signal) ---
    df["setup_condition"] = (
        df["near_resistance"]
        & df["is_squeezing"]
        & df["above_trend"]
        & df["volume_building"]
        & df["liquid_enough"]
        & (~df["broke_out_today"])   # don't flag as "setup" if it already broke out
    )

    df["atr"] = compute_atr(df, cfg.atr_len)
    return df


# ==================== SCREENER ====================

def screen_watchlist(tickers: list[str], cfg: BreakoutConfig, lookback_days: int = 300) -> pd.DataFrame:
    end = datetime.today()
    start = end - timedelta(days=lookback_days)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    rows = []
    for ticker in tickers:
        try:
            print(f"Checking {ticker}...")
            df = fetch_daily_data(ticker, start_str, end_str)
            sig = build_signal_frame(df, cfg)
            latest = sig.iloc[-1]

            status = "-"
            if bool(latest["broke_out_today"]):
                status = "BROKEN OUT"
            elif bool(latest["setup_condition"]):
                status = "SETUP (watch)"

            rows.append({
                "ticker": ticker,
                "close": round(float(latest["close"]), 2),
                "resistance": round(float(latest["resistance"]), 2) if pd.notna(latest["resistance"]) else np.nan,
                "%_below_resistance": round(float(latest["pct_below_resistance"]), 2)
                    if pd.notna(latest["pct_below_resistance"]) else np.nan,
                "is_squeezing": bool(latest["is_squeezing"]) if pd.notna(latest["is_squeezing"]) else False,
                "volume_building": bool(latest["volume_building"]) if pd.notna(latest["volume_building"]) else False,
                "above_trend": bool(latest["above_trend"]) if pd.notna(latest["above_trend"]) else False,
                "STATUS": status,
                "breakout_buy_level": round(float(latest["resistance"]), 2) if pd.notna(latest["resistance"]) else np.nan,
                "suggested_stop_if_entered": round(float(latest["close"] - cfg.atr_mult * latest["atr"]), 2)
                    if pd.notna(latest["atr"]) else np.nan,
            })
        except Exception as e:
            print(f"  Skipping {ticker}: {e}")

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values("%_below_resistance", ascending=True, na_position="last").reset_index(drop=True)
    return result


# ==================== BACKTESTER ====================

def backtest_ticker(ticker: str, cfg: BreakoutConfig, start: str, end: str = None,
                     initial_capital: float = 10_000, commission_pct: float = 0.05,
                     entry_mode: str = "breakout") -> dict:
    """
    entry_mode:
      "breakout" -> enters when price actually closes above resistance
                    (i.e., trades the confirmed breakout, using the setup
                    condition on the prior day as a pre-filter)
      "setup"    -> enters as soon as setup_condition is true (more
                    aggressive, buys the coil before confirmation)
    """
    end = end or datetime.today().strftime("%Y-%m-%d")

    # The indicators need history *before* the requested start date to warm up
    # (resistance/trend need up to `resistance_window`/`trend_ma_len` bars, and the
    # squeeze percentile needs `squeeze_lookback + squeeze_window` bars). Without this
    # buffer every rolling indicator stays NaN for the whole backtest window and no
    # trade can ever trigger. Pull extra calendar days to cover the longest warm-up.
    warmup_bars = max(
        cfg.resistance_window,
        cfg.trend_ma_len,
        cfg.squeeze_lookback + cfg.squeeze_window,
        cfg.vol_avg_len * 3,
    ) + cfg.atr_len
    warmup_days = int(warmup_bars * 1.6) + 30  # trading days -> calendar days, plus margin
    fetch_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=warmup_days)).strftime("%Y-%m-%d")

    df = fetch_daily_data(ticker, fetch_start, end)
    sig = build_signal_frame(df, cfg)
    start_ts = pd.Timestamp(start)

    position = 0
    entry_price = None
    entry_idx = None
    trail_stop = None
    trades = []
    equity = initial_capital
    equity_curve = []

    dates = sig.index
    for i, date in enumerate(dates):
        row = sig.loc[date]
        close = row["close"]

        if position == 0:
            if date < start_ts:
                continue  # warm-up-only bar: indicators may still be settling, don't enter here
            triggered = row["broke_out_today"] if entry_mode == "breakout" else row["setup_condition"]
            if bool(triggered) if pd.notna(triggered) else False:
                position = 1
                entry_price = close * (1 + commission_pct / 100)
                entry_idx = i
                trail_stop = close - cfg.atr_mult * row["atr"] if pd.notna(row["atr"]) else None
                trades.append({"entry_date": date, "entry_price": entry_price, "mode": entry_mode})
        else:
            if pd.notna(row["atr"]):
                new_stop = close - cfg.atr_mult * row["atr"]
                trail_stop = max(trail_stop, new_stop) if trail_stop is not None else new_stop

            stop_hit = trail_stop is not None and row["low"] <= trail_stop

            if stop_hit:
                exit_price = trail_stop * (1 - commission_pct / 100)
                pnl_pct = (exit_price / entry_price - 1) * 100
                equity *= (exit_price / entry_price)
                trades[-1].update({
                    "exit_date": date,
                    "exit_price": exit_price,
                    "pnl_%": round(pnl_pct, 2),
                    "reason": "stop_hit",
                })
                position = 0
                entry_price = None
                trail_stop = None

        equity_curve.append({"date": date, "equity": equity})

    completed = [t for t in trades if "exit_date" in t]
    win_trades = [t for t in completed if t["pnl_%"] > 0]

    unrealized_equity = equity
    if position == 1 and entry_price is not None:
        last_close = sig["close"].iloc[-1]
        unrealized_equity = equity * (last_close / entry_price)
        open_trade = trades[-1]
        open_trade["mark_price"] = round(float(last_close), 2)
        open_trade["unrealized_pnl_%"] = round(float((last_close / entry_price - 1) * 100), 2)

    summary = {
        "ticker": ticker,
        "entry_mode": entry_mode,
        "total_trades_completed": len(completed),
        "open_position": position == 1,
        "win_rate_%": round(100 * len(win_trades) / len(completed), 1) if completed else None,
        "avg_pnl_%_completed": round(np.mean([t["pnl_%"] for t in completed]), 2) if completed else None,
        "realized_return_%": round((equity / initial_capital - 1) * 100, 2),
        "total_return_incl_open_%": round((unrealized_equity / initial_capital - 1) * 100, 2),
    }

    return {"summary": summary, "trades": trades, "equity_curve": pd.DataFrame(equity_curve)}


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="Breakout screener & backtester")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--universe", choices=["penny", "midcap", "bluechip"], default=None,
                         help="Blind-search the whole market in this cap bucket via Yahoo's "
                              "screener instead of using --tickers/the default watchlist")
    parser.add_argument("--universe-limit", type=int, default=100,
                         help="Max tickers to pull from --universe (default 100)")
    parser.add_argument("--resistance-window", type=int, default=50)
    parser.add_argument("--data-source", choices=["yahoo", "twelvedata", "alpaca"], default="yahoo",
                        help="Which data provider to use for price history")
    parser.add_argument("--near-resistance-pct", type=float, default=3.0)
    parser.add_argument("--min-volume", type=float, default=1_000_000)
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--entry-mode", choices=["breakout", "setup"], default="breakout",
                         help="'breakout' = trade confirmed breakouts; 'setup' = trade the coil before confirmation")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    cfg = BreakoutConfig(
        resistance_window=args.resistance_window,
        near_resistance_pct=args.near_resistance_pct,
        min_avg_volume=args.min_volume,
    )

    # set global data source
    global DATA_SOURCE
    DATA_SOURCE = args.data_source

    if args.universe:
        print(f"Blind-searching {args.universe} universe (min volume {args.min_volume:,.0f})...")
        args.tickers = fetch_universe_tickers(args.universe, args.min_volume, args.universe_limit)
        if not args.tickers:
            print("No tickers matched the universe filter.")
            return
        print(f"Found {len(args.tickers)} tickers: {', '.join(args.tickers)}\n")
    elif args.tickers is None:
        args.tickers = DEFAULT_WATCHLIST

    if args.backtest:
        for ticker in args.tickers:
            print(f"\n=== Backtesting {ticker} ({args.entry_mode} mode, {args.start} to {args.end or 'today'}) ===")
            try:
                result = backtest_ticker(ticker, cfg, args.start, args.end, entry_mode=args.entry_mode)
                print(pd.Series(result["summary"]))
                if result["trades"]:
                    print("\nTrade log:")
                    print(pd.DataFrame(result["trades"]).to_string(index=False))
            except Exception as e:
                print(f"  Error backtesting {ticker}: {e}")
    else:
        print(f"\nScreening {len(args.tickers)} tickers for breakout setups...\n")
        results = screen_watchlist(args.tickers, cfg)
        if results.empty:
            print("No data returned.")
            return
        pd.set_option("display.width", 160)
        pd.set_option("display.max_columns", None)
        print("\n" + results.to_string(index=False))

        setups = results[results["STATUS"] == "SETUP (watch)"]
        broken = results[results["STATUS"] == "BROKEN OUT"]
        print(f"\n{len(setups)} ticker(s) coiling near resistance (watchlist): "
              f"{', '.join(setups['ticker'].tolist()) if not setups.empty else 'none'}")
        print(f"{len(broken)} ticker(s) already broke out today: "
              f"{', '.join(broken['ticker'].tolist()) if not broken.empty else 'none'}")

        if args.csv:
            results.to_csv(args.csv, index=False)
            print(f"\nSaved results to {args.csv}")


if __name__ == "__main__":
    main()