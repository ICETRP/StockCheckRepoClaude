"""
RS Swing Leaders — Relative Strength Screener & Backtester
=============================================================
Replicates the "RS Swing Leaders (vs Benchmark + Volume Filter)" Pine Script
strategy in Python, using yfinance for daily OHLCV data.

Strategy logic:
  1. Relative Strength: stock's % return over N bars > benchmark's % return
  2. RS line (EMA of price ratio) is rising and at an N-bar high
  3. Trend filter: price above its own SMA
  4. Liquidity filter: average daily volume above a minimum threshold
  5. Exit: ATR-based trailing stop, or RS breakdown (stock starts underperforming)

Install requirements first:
    pip install yfinance pandas numpy

Usage:
    python rs_swing_leaders.py                       # screen default watchlist
    python rs_swing_leaders.py --tickers NVDA AMD META AVGO TSLA
    python rs_swing_leaders.py --backtest --tickers NVDA --start 2023-01-01
"""

import argparse
import sys
import os
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("Missing dependency. Install it with:\n    pip install yfinance pandas numpy")
    sys.exit(1)

# optional: load .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DATA_PROVIDER = os.getenv("DATA_PROVIDER", "yahoo").lower()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://data.alpaca.markets")


# ==================== CONFIG ====================

@dataclass
class StrategyConfig:
    benchmark: str = "SPY"
    rs_lookback: int = 20          # bars for return comparison (~1 month on daily)
    rs_smooth_len: int = 7         # EMA smoothing of the RS line
    rs_high_lookback: int = 20     # lookback for "RS line at N-bar high" check
    trend_ma_len: int = 50         # trend filter MA length
    vol_avg_len: int = 25          # average volume lookback (days)
    min_avg_volume: float = 1_000_000  # minimum average daily volume (shares)
    atr_len: int = 14
    atr_mult: float = 2.75         # trailing stop = close - atr_mult * ATR
    require_new_high_rs: bool = True


DEFAULT_WATCHLIST = [
    "NVDA", "AMD", "META", "AVGO", "MSFT", "AAPL", "GOOGL",
    "AMZN", "TSLA", "NFLX", "CRM", "ORCL",
]


# ==================== DATA FETCHING ====================

def fetch_daily_data_yahoo(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily OHLCV data for a single ticker via yfinance."""
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}. Check the symbol and date range.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close", "volume"]].dropna()


def fetch_daily_data_twelvedata(ticker: str, start: str, end: str) -> pd.DataFrame:
    if not TWELVEDATA_API_KEY:
        raise ValueError("TWELVEDATA_API_KEY not set in environment (.env)")
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": ticker,
        "interval": "1day",
        "start_date": start,
        "end_date": end,
        "outputsize": 5000,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise ValueError(f"Error calling Twelve Data for {ticker}: {e}")

    if data is None:
        raise ValueError(f"No response from Twelve Data for {ticker}")
    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error for {ticker}: {data.get('message')}")

    values = data.get("values") or []
    if not values:
        raise ValueError(f"No price data returned for {ticker} from Twelve Data.")

    df = pd.DataFrame(values)

    # normalize datetime-like column to index
    index_candidates = [c for c in ("datetime", "date", "timestamp", "time") if c in df.columns]
    if index_candidates:
        dt_col = index_candidates[0]
        df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")
        df = df.dropna(subset=[dt_col])
        df = df.set_index(dt_col)
    else:
        try:
            df.index = pd.to_datetime(df.index, errors="coerce")
        except Exception:
            pass

    if isinstance(df.index, pd.PeriodIndex):
        df.index = df.index.to_timestamp()

    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    df = df[~df.index.isna()]
    if df.empty:
        raise ValueError(f"No valid datetime rows returned for {ticker} from Twelve Data.")
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["open", "high", "low", "close", "volume"]].dropna()


def fetch_daily_data_alpaca(ticker: str, start: str, end: str) -> pd.DataFrame:
    key = ALPACA_API_KEY
    secret = ALPACA_API_SECRET
    base = ALPACA_BASE_URL.rstrip('/')
    if not key or not secret:
        raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET must be set in env or passed via CLI")
    url = f"{base}/v2/stocks/{ticker}/bars"
    params = {"timeframe": "1Day", "start": start, "end": end, "limit": 5000}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise ValueError(f"Error calling Alpaca for {ticker}: {e}")

    values = data.get("bars") or []
    if not values:
        raise ValueError(f"No price data returned for {ticker} from Alpaca.")
    df = pd.DataFrame(values)
    time_col = next((c for c in ("t", "timestamp", "datetime", "time") if c in df.columns), None)
    if time_col:
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        df = df.dropna(subset=[time_col])
        df = df.set_index(time_col)
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()
    mapping = {}
    if "o" in df.columns:
        mapping.update({"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.rename(columns=mapping)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["open", "high", "low", "close", "volume"]].dropna()


def fetch_daily_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    provider = DATA_PROVIDER or "yahoo"
    if provider.startswith("twelv") or provider == "twelvedata" or provider == "twelve":
        return fetch_daily_data_twelvedata(ticker, start, end)
    if provider.startswith("alpaca") or provider == "alpaca":
        return fetch_daily_data_alpaca(ticker, start, end)
    return fetch_daily_data_yahoo(ticker, start, end)


# ==================== INDICATOR CALCULATIONS ====================

def compute_atr(df: pd.DataFrame, length: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def build_signal_frame(stock_df: pd.DataFrame, bench_df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """
    Aligns stock and benchmark data, computes every filter, and returns
    a single dataframe with a boolean 'long_condition' column plus all
    intermediate signals for inspection/debugging.
    """
    df = stock_df.join(bench_df["close"].rename("bench_close"), how="inner")

    # --- Relative strength ratio & smoothed RS line ---
    df["rs_ratio"] = df["close"] / df["bench_close"]
    df["rs_line"] = df["rs_ratio"].ewm(span=cfg.rs_smooth_len, adjust=False).mean()

    # --- N-bar return comparison (stock vs benchmark) ---
    df["stock_ret"] = df["close"].pct_change(cfg.rs_lookback) * 100
    df["bench_ret"] = df["bench_close"].pct_change(cfg.rs_lookback) * 100
    df["outperforming"] = df["stock_ret"] > df["bench_ret"]

    # --- RS line rising / at N-bar high ---
    df["rs_rising"] = df["rs_line"] > df["rs_line"].shift(1)
    df["rs_new_high"] = df["rs_line"] >= df["rs_line"].rolling(cfg.rs_high_lookback).max()
    df["rs_strong"] = (df["rs_rising"] & df["rs_new_high"]) if cfg.require_new_high_rs else df["rs_rising"]

    # --- Trend filter ---
    df["trend_ma"] = df["close"].rolling(cfg.trend_ma_len).mean()
    df["price_above"] = df["close"] > df["trend_ma"]

    # --- Liquidity filter ---
    df["avg_volume"] = df["volume"].rolling(cfg.vol_avg_len).mean()
    df["liquid_enough"] = df["avg_volume"] >= cfg.min_avg_volume

    # --- ATR for trailing stop ---
    df["atr"] = compute_atr(df, cfg.atr_len)

    # --- Final entry condition ---
    df["long_condition"] = (
        df["outperforming"]
        & df["rs_strong"]
        & df["price_above"]
        & df["liquid_enough"]
    )

    return df


# ==================== SCREENER ====================

def screen_watchlist(tickers: list[str], cfg: StrategyConfig, lookback_days: int = 400) -> pd.DataFrame:
    """
    Runs the strategy on each ticker and reports the CURRENT (most recent bar)
    signal status for each, ranked by relative strength.
    """
    end = datetime.today()
    start = end - timedelta(days=lookback_days)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    print(f"Fetching benchmark ({cfg.benchmark})...")
    bench_df = fetch_daily_data(cfg.benchmark, start_str, end_str)

    rows = []
    for ticker in tickers:
        try:
            print(f"Fetching {ticker}...")
            stock_df = fetch_daily_data(ticker, start_str, end_str)
            sig = build_signal_frame(stock_df, bench_df, cfg)
            latest = sig.iloc[-1]

            rows.append({
                "ticker": ticker,
                "close": round(latest["close"], 2),
                "stock_ret_%": round(latest["stock_ret"], 2) if pd.notna(latest["stock_ret"]) else np.nan,
                "bench_ret_%": round(latest["bench_ret"], 2) if pd.notna(latest["bench_ret"]) else np.nan,
                "outperforming": bool(latest["outperforming"]),
                "rs_strong": bool(latest["rs_strong"]),
                "above_trend_ma": bool(latest["price_above"]),
                "avg_volume": int(latest["avg_volume"]) if pd.notna(latest["avg_volume"]) else np.nan,
                "liquid_enough": bool(latest["liquid_enough"]),
                "SIGNAL": "LONG" if bool(latest["long_condition"]) else "-",
                "suggested_stop": round(latest["close"] - cfg.atr_mult * latest["atr"], 2)
                    if pd.notna(latest["atr"]) else np.nan,
            })
        except Exception as e:
            print(f"  Skipping {ticker}: {e}")

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values("stock_ret_%", ascending=False).reset_index(drop=True)
    return result


# ==================== SIMPLE BACKTESTER ====================

def backtest_ticker(ticker: str, cfg: StrategyConfig, start: str, end: str = None,
                     initial_capital: float = 10_000, commission_pct: float = 0.05) -> dict:
    """
    Simple long-only backtest for one ticker:
      - Enters full position when long_condition first turns True (flat -> long)
      - Exits on ATR trailing stop hit OR relative-strength breakdown
    Returns a dict with trade list and summary stats.
    """
    end = end or datetime.today().strftime("%Y-%m-%d")
    bench_df = fetch_daily_data(cfg.benchmark, start, end)
    stock_df = fetch_daily_data(ticker, start, end)
    df = build_signal_frame(stock_df, bench_df, cfg)

    position = 0          # 0 = flat, 1 = long
    entry_price = None
    trail_stop = None
    trades = []
    equity = initial_capital
    equity_curve = []

    for date, row in df.iterrows():
        close = row["close"]

        if position == 0:
            if row["long_condition"]:
                position = 1
                entry_price = close * (1 + commission_pct / 100)
                trail_stop = close - cfg.atr_mult * row["atr"] if pd.notna(row["atr"]) else None
                trades.append({"entry_date": date, "entry_price": entry_price})
        else:
            # update trailing stop (only ever moves up)
            if pd.notna(row["atr"]):
                new_stop = close - cfg.atr_mult * row["atr"]
                trail_stop = max(trail_stop, new_stop) if trail_stop is not None else new_stop

            rs_breakdown = (row["rs_ratio"] < row["rs_line"]) and (not row["outperforming"])
            stop_hit = trail_stop is not None and row["low"] <= trail_stop

            if stop_hit or rs_breakdown:
                exit_price = trail_stop if stop_hit else close
                exit_price = exit_price * (1 - commission_pct / 100)
                pnl_pct = (exit_price / entry_price - 1) * 100
                equity *= (exit_price / entry_price)
                trades[-1].update({
                    "exit_date": date,
                    "exit_price": exit_price,
                    "pnl_%": round(pnl_pct, 2),
                    "reason": "stop_hit" if stop_hit else "rs_breakdown",
                })
                position = 0
                entry_price = None
                trail_stop = None

        equity_curve.append({"date": date, "equity": equity})

    completed = [t for t in trades if "exit_date" in t]
    win_trades = [t for t in completed if t["pnl_%"] > 0]

    summary = {
        "ticker": ticker,
        "total_trades": len(completed),
        "open_position": position == 1,
        "win_rate_%": round(100 * len(win_trades) / len(completed), 1) if completed else None,
        "avg_pnl_%": round(np.mean([t["pnl_%"] for t in completed]), 2) if completed else None,
        "final_equity": round(equity, 2),
        "total_return_%": round((equity / initial_capital - 1) * 100, 2),
    }

    return {"summary": summary, "trades": trades, "equity_curve": pd.DataFrame(equity_curve)}


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="RS Swing Leaders screener & backtester")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_WATCHLIST,
                         help="Tickers to screen/backtest")
    parser.add_argument("--data-provider", choices=["yahoo", "twelvedata", "alpaca"], default=None,
                         help="Data provider to use for price data (overrides DATA_PROVIDER env)")
    parser.add_argument("--td-api-key", default=None,
                         help="Twelve Data API key (overrides TWELVEDATA_API_KEY env)")
    parser.add_argument("--alpaca-key", default=None, help="Alpaca API key (overrides ALPACA_API_KEY env)")
    parser.add_argument("--alpaca-secret", default=None, help="Alpaca API secret (overrides ALPACA_API_SECRET env)")
    parser.add_argument("--alpaca-base", default=None, help="Alpaca base URL (overrides ALPACA_BASE_URL env)")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol")
    parser.add_argument("--min-volume", type=float, default=1_000_000,
                         help="Minimum average daily volume filter")
    parser.add_argument("--backtest", action="store_true",
                         help="Run backtest instead of live screener (single ticker recommended)")
    parser.add_argument("--start", default="2023-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Backtest end date (YYYY-MM-DD), default today")
    parser.add_argument("--csv", default=None, help="Optional path to save screener results as CSV")
    args = parser.parse_args()

    cfg = StrategyConfig(benchmark=args.benchmark, min_avg_volume=args.min_volume)

    # allow CLI to override provider and API key
    if args.data_provider:
        DATA_PROVIDER = args.data_provider.lower()
    if args.td_api_key:
        TWELVEDATA_API_KEY = args.td_api_key
    if args.alpaca_key:
        ALPACA_API_KEY = args.alpaca_key
    if args.alpaca_secret:
        ALPACA_API_SECRET = args.alpaca_secret
    if args.alpaca_base:
        ALPACA_BASE_URL = args.alpaca_base

    if args.backtest:
        for ticker in args.tickers:
            print(f"\n=== Backtesting {ticker} ({args.start} to {args.end or 'today'}) ===")
            result = backtest_ticker(ticker, cfg, args.start, args.end)
            print(pd.Series(result["summary"]))
            if result["trades"]:
                print("\nTrade log:")
                print(pd.DataFrame(result["trades"]).to_string(index=False))
    else:
        print(f"\nScreening {len(args.tickers)} tickers for RS Swing Leader signals...\n")
        results = screen_watchlist(args.tickers, cfg)
        if results.empty:
            print("No data returned.")
            return
        pd.set_option("display.width", 160)
        pd.set_option("display.max_columns", None)
        print("\n" + results.to_string(index=False))

        longs = results[results["SIGNAL"] == "LONG"]
        print(f"\n{len(longs)} ticker(s) currently meeting all entry criteria: "
              f"{', '.join(longs['ticker'].tolist()) if not longs.empty else 'none'}")

        if args.csv:
            results.to_csv(args.csv, index=False)
            print(f"\nSaved results to {args.csv}")


if __name__ == "__main__":
    main()