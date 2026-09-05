"""
Earnings-Beat Momentum (Post-Earnings-Announcement Drift) — Screener & Backtester
====================================================================================
Strategy: buy/hold quality large & mid-cap stocks that just posted a strong
EPS beat AND got a positive market reaction (a practical proxy for "beat and
raised guidance", since exact guidance data isn't reliably available for
free). Ride the well-documented post-earnings drift for several weeks,
exiting on a trailing stop or after a maximum hold period.

Why this proxy works:
  Academic research on Post-Earnings-Announcement Drift (PEAD) shows stocks
  that beat estimates AND jump on the earnings reaction day tend to keep
  drifting upward for 60-90 days afterward, because the market underreacts
  to the full information content of a strong beat + raised outlook.

Filters used:
  1. Reported EPS beat estimate by at least `min_eps_surprise_pct`
  2. Price reaction on the trading day after earnings was positive by at
     least `min_reaction_pct` (proxy for "market liked the guidance too")
  3. Earnings happened within the last `lookback_days` trading days
     (so you're still early in the drift window, not chasing an old move)
  4. Price is above its own trend MA (avoid buying into a reversal)
  5. Average daily volume clears a liquidity minimum

Install requirements first:
    pip install yfinance pandas numpy

Usage:
    python earnings_beat_momentum.py
    python earnings_beat_momentum.py --tickers NVDA AMD AVGO MSFT GOOGL
    python earnings_beat_momentum.py --backtest --tickers NVDA --start 2022-01-01
"""

import argparse
import sys
import os
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta
import os

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

# Data provider selection: 'yahoo' (yfinance) or 'twelvedata'
DATA_PROVIDER = os.getenv("DATA_PROVIDER", "yahoo").lower()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://data.alpaca.markets")


# ==================== CONFIG ====================

@dataclass
class PEADConfig:
    min_eps_surprise_pct: float = 5.0      # minimum EPS beat, in %
    min_reaction_pct: float = 2.0          # minimum next-day price pop, in %
    lookback_days: int = 10                # only flag earnings within N trading days
    trend_ma_len: int = 50                 # trend filter MA length
    vol_avg_len: int = 25                  # average volume lookback (days)
    min_avg_volume: float = 1_000_000      # minimum average daily volume (shares)
    atr_len: int = 14
    atr_mult: float = 3.0                  # wider stop than pure swing-RS strategy,
                                            # since PEAD holds are meant to run longer
    max_hold_days: int = 60                # PEAD drift window; exit if stop not hit by then


DEFAULT_WATCHLIST = [
    "NVDA", "AMD", "AVGO", "MSFT", "AAPL", "GOOGL", "META",
    "AMZN", "CRM", "ORCL", "MU", "LRCX", "ANET", "PANW",
]


# ==================== DATA FETCHING ====================

def fetch_daily_data_yahoo(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily OHLCV data for a single ticker via yfinance."""
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No price data returned for {ticker}.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close", "volume"]].dropna()


def fetch_daily_data_twelvedata(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily OHLCV data using the Twelve Data REST API.

    Requires `TWELVEDATA_API_KEY` in environment or .env.
    """
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

    # detect possible datetime-like column names and normalize to index
    index_candidates = [c for c in ("datetime", "date", "timestamp", "time") if c in df.columns]
    if index_candidates:
        dt_col = index_candidates[0]
        # coerce to datetime, drop rows that fail
        df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")
        df = df.dropna(subset=[dt_col])
        df = df.set_index(dt_col)
    else:
        # if Twelve Data already provided an index-like structure, try coercion
        try:
            df.index = pd.to_datetime(df.index, errors="coerce")
            df = df.dropna(axis=0, subset=[df.columns[0]]) if df.index.isna().any() else df
        except Exception:
            pass

    # If index is PeriodIndex, convert to timestamps
    if isinstance(df.index, pd.PeriodIndex):
        df.index = df.index.to_timestamp()

    # Ensure a plain, timezone-naive DatetimeIndex sorted ascending
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    df = df[~df.index.isna()]
    if df.empty:
        raise ValueError(f"No valid datetime rows returned for {ticker} from Twelve Data.")
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    # map expected columns and coerce types
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df[["open", "high", "low", "close", "volume"]].dropna()


def fetch_daily_data_alpaca(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily OHLCV using Alpaca market data API.

    Accepts `ALPACA_API_KEY`, `ALPACA_API_SECRET`, and optional `ALPACA_BASE_URL`.
    """
    key = ALPACA_API_KEY
    secret = ALPACA_API_SECRET
    base = ALPACA_BASE_URL.rstrip('/')
    if not key or not secret:
        raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET must be set in env or passed via CLI")

    url = f"{base}/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Day",
        "start": start,
        "end": end,
        "limit": 5000,
    }
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise ValueError(f"Error calling Alpaca for {ticker}: {e}")

    values = data.get("bars") or data.get("bars") or []
    if not values:
        raise ValueError(f"No price data returned for {ticker} from Alpaca.")

    df = pd.DataFrame(values)
    # Alpaca bars commonly use keys: t, o, h, l, c, v
    time_col = next((c for c in ("t", "timestamp", "datetime", "time") if c in df.columns), None)
    if time_col:
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        df = df.dropna(subset=[time_col])
        df = df.set_index(time_col)

    # normalize index
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    # map columns
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


def fetch_earnings_history(ticker: str) -> pd.DataFrame:
    """
    Fetch historical earnings dates + EPS estimate/actual/surprise% via yfinance.
    Returns a dataframe indexed by earnings date, sorted ascending, with only
    rows that have an actual reported EPS (i.e. earnings already happened).
    """
    tk = yf.Ticker(ticker)
    try:
        earnings = tk.get_earnings_dates(limit=40)
    except Exception as e:
        raise ValueError(f"Could not fetch earnings dates for {ticker}: {e}")

    if earnings is None or earnings.empty:
        raise ValueError(f"No earnings history available for {ticker}.")

    earnings = earnings.rename(columns={
        "EPS Estimate": "eps_estimate",
        "Reported EPS": "eps_actual",
        "Surprise(%)": "eps_surprise_pct",
    })
    earnings.index = pd.to_datetime(earnings.index).tz_localize(None)
    earnings = earnings.sort_index()
    earnings = earnings.dropna(subset=["eps_actual"])  # keep only reported (past) earnings
    return earnings


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


def find_latest_qualifying_earnings(price_df: pd.DataFrame, earnings_df: pd.DataFrame,
                                     cfg: PEADConfig, as_of: pd.Timestamp = None):
    """
    Looks at the most recent earnings events and checks whether the EPS
    surprise + next-day price reaction both cleared the strategy's thresholds.
    Returns a dict describing the latest qualifying event, or None.
    """
    as_of = as_of or price_df.index[-1]
    trading_days = price_df.index

    candidates = earnings_df[earnings_df.index <= as_of].tail(6)  # check a handful of recent reports

    for earnings_date in reversed(candidates.index):
        row = candidates.loc[earnings_date]
        eps_surprise = row.get("eps_surprise_pct", np.nan)
        if pd.isna(eps_surprise) or eps_surprise < cfg.min_eps_surprise_pct:
            continue

        # find the first trading day on/after the earnings date, and the day after that
        future_days = trading_days[trading_days >= earnings_date]
        if len(future_days) < 2:
            continue
        reaction_day = future_days[1]  # next full trading session after the report
        pre_earnings_days = trading_days[trading_days < earnings_date]
        if len(pre_earnings_days) < 1:
            continue
        pre_close = price_df.loc[pre_earnings_days[-1], "close"]
        reaction_close = price_df.loc[reaction_day, "close"]
        reaction_pct = (reaction_close / pre_close - 1) * 100

        if reaction_pct < cfg.min_reaction_pct:
            continue

        days_since = trading_days.get_loc(as_of) - trading_days.get_loc(reaction_day)
        if days_since > cfg.lookback_days or days_since < 0:
            continue

        return {
            "earnings_date": earnings_date,
            "reaction_day": reaction_day,
            "eps_surprise_pct": round(float(eps_surprise), 2),
            "reaction_pct": round(float(reaction_pct), 2),
            "days_since_reaction": int(days_since),
        }

    return None


def build_screen_row(ticker: str, cfg: PEADConfig, lookback_price_days: int = 300) -> dict:
    end = datetime.today()
    start = end - timedelta(days=lookback_price_days)
    price_df = fetch_daily_data(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    earnings_df = fetch_earnings_history(ticker)

    price_df["trend_ma"] = price_df["close"].rolling(cfg.trend_ma_len).mean()
    price_df["avg_volume"] = price_df["volume"].rolling(cfg.vol_avg_len).mean()
    price_df["atr"] = compute_atr(price_df, cfg.atr_len)

    event = find_latest_qualifying_earnings(price_df, earnings_df, cfg)
    latest = price_df.iloc[-1]

    above_trend = bool(latest["close"] > latest["trend_ma"]) if pd.notna(latest["trend_ma"]) else False
    liquid_enough = bool(latest["avg_volume"] >= cfg.min_avg_volume) if pd.notna(latest["avg_volume"]) else False

    signal = bool(event is not None and above_trend and liquid_enough)

    return {
        "ticker": ticker,
        "close": round(float(latest["close"]), 2),
        "eps_surprise_%": event["eps_surprise_pct"] if event else np.nan,
        "earnings_reaction_%": event["reaction_pct"] if event else np.nan,
        "days_since_reaction": event["days_since_reaction"] if event else np.nan,
        "above_trend_ma": above_trend,
        "liquid_enough": liquid_enough,
        "SIGNAL": "BUY/HOLD" if signal else "-",
        "suggested_stop": round(float(latest["close"] - cfg.atr_mult * latest["atr"]), 2)
            if pd.notna(latest["atr"]) else np.nan,
    }


# ==================== SCREENER ====================

def screen_watchlist(tickers: list[str], cfg: PEADConfig) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        try:
            print(f"Checking {ticker}...")
            rows.append(build_screen_row(ticker, cfg))
        except Exception as e:
            print(f"  Skipping {ticker}: {e}")

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values("eps_surprise_%", ascending=False, na_position="last").reset_index(drop=True)
    return result


# ==================== BACKTESTER ====================

def backtest_ticker(ticker: str, cfg: PEADConfig, start: str, end: str = None,
                     initial_capital: float = 10_000, commission_pct: float = 0.05) -> dict:
    """
    Long-only PEAD backtest for one ticker:
      - Enters on the reaction day of any qualifying earnings beat
      - Exits on ATR trailing stop OR after cfg.max_hold_days, whichever comes first
    """
    end = end or datetime.today().strftime("%Y-%m-%d")
    price_df = fetch_daily_data(ticker, start, end)
    earnings_df = fetch_earnings_history(ticker)

    price_df["trend_ma"] = price_df["close"].rolling(cfg.trend_ma_len).mean()
    price_df["avg_volume"] = price_df["volume"].rolling(cfg.vol_avg_len).mean()
    price_df["atr"] = compute_atr(price_df, cfg.atr_len)

    # Precompute all qualifying entry events across the whole backtest window
    entry_events = []
    for earnings_date in earnings_df.index:
        row = earnings_df.loc[earnings_date]
        eps_surprise = row.get("eps_surprise_pct", np.nan)
        if pd.isna(eps_surprise) or eps_surprise < cfg.min_eps_surprise_pct:
            continue
        future_days = price_df.index[price_df.index >= earnings_date]
        pre_days = price_df.index[price_df.index < earnings_date]
        if len(future_days) < 2 or len(pre_days) < 1:
            continue
        reaction_day = future_days[1]
        pre_close = price_df.loc[pre_days[-1], "close"]
        reaction_close = price_df.loc[reaction_day, "close"]
        reaction_pct = (reaction_close / pre_close - 1) * 100
        if reaction_pct < cfg.min_reaction_pct:
            continue
        entry_events.append(reaction_day)

    entry_events = sorted(set(entry_events))

    position = 0
    entry_price = None
    entry_idx = None
    trail_stop = None
    trades = []
    equity = initial_capital
    equity_curve = []

    dates = price_df.index
    for i, date in enumerate(dates):
        close = price_df.loc[date, "close"]

        if position == 0:
            if date in entry_events and pd.notna(price_df.loc[date, "trend_ma"]) \
                    and close > price_df.loc[date, "trend_ma"] \
                    and pd.notna(price_df.loc[date, "avg_volume"]) \
                    and price_df.loc[date, "avg_volume"] >= cfg.min_avg_volume:
                position = 1
                entry_price = close * (1 + commission_pct / 100)
                entry_idx = i
                atr_val = price_df.loc[date, "atr"]
                trail_stop = close - cfg.atr_mult * atr_val if pd.notna(atr_val) else None
                trades.append({"entry_date": date, "entry_price": entry_price})
        else:
            atr_val = price_df.loc[date, "atr"]
            if pd.notna(atr_val):
                new_stop = close - cfg.atr_mult * atr_val
                trail_stop = max(trail_stop, new_stop) if trail_stop is not None else new_stop

            stop_hit = trail_stop is not None and price_df.loc[date, "low"] <= trail_stop
            time_exit = (i - entry_idx) >= cfg.max_hold_days

            if stop_hit or time_exit:
                exit_price = trail_stop if stop_hit else close
                exit_price = exit_price * (1 - commission_pct / 100)
                pnl_pct = (exit_price / entry_price - 1) * 100
                equity *= (exit_price / entry_price)
                trades[-1].update({
                    "exit_date": date,
                    "exit_price": exit_price,
                    "pnl_%": round(pnl_pct, 2),
                    "reason": "stop_hit" if stop_hit else "max_hold_reached",
                })
                position = 0
                entry_price = None
                trail_stop = None

        equity_curve.append({"date": date, "equity": equity})

    completed = [t for t in trades if "exit_date" in t]
    win_trades = [t for t in completed if t["pnl_%"] > 0]

    # --- Mark any still-open position to the last available close (unrealized P&L) ---
    unrealized_equity = equity
    if position == 1 and entry_price is not None:
        last_close = price_df["close"].iloc[-1]
        last_date = price_df.index[-1]
        unrealized_pnl_pct = (last_close / entry_price - 1) * 100
        unrealized_equity = equity * (last_close / entry_price)

        open_trade = trades[-1]
        open_trade["mark_date"] = last_date
        open_trade["mark_price"] = round(float(last_close), 2)
        open_trade["unrealized_pnl_%"] = round(float(unrealized_pnl_pct), 2)
        open_trade["current_stop"] = round(float(trail_stop), 2) if trail_stop is not None else None

        days_held = len(price_df) - 1 - entry_idx
        open_trade["days_held"] = days_held
        open_trade["days_left_in_max_hold"] = max(cfg.max_hold_days - days_held, 0)

    summary = {
        "ticker": ticker,
        "total_trades_completed": len(completed),
        "open_position": position == 1,
        "win_rate_%": round(100 * len(win_trades) / len(completed), 1) if completed else None,
        "avg_pnl_%_completed": round(np.mean([t["pnl_%"] for t in completed]), 2) if completed else None,
        "realized_equity": round(equity, 2),
        "realized_return_%": round((equity / initial_capital - 1) * 100, 2),
        "unrealized_equity_incl_open": round(unrealized_equity, 2),
        "total_return_incl_open_%": round((unrealized_equity / initial_capital - 1) * 100, 2),
    }

    return {"summary": summary, "trades": trades, "equity_curve": pd.DataFrame(equity_curve)}


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="Earnings-Beat Momentum (PEAD) screener & backtester")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_WATCHLIST,
                         help="Tickers to screen/backtest")
    parser.add_argument("--data-provider", choices=["yahoo", "twelvedata", "alpaca"], default=None,
                         help="Data provider to use for price data (overrides DATA_PROVIDER env)")
    parser.add_argument("--td-api-key", default=None,
                         help="Twelve Data API key (overrides TWELVEDATA_API_KEY env)")
    parser.add_argument("--alpaca-key", default=None, help="Alpaca API key (overrides ALPACA_API_KEY env)")
    parser.add_argument("--alpaca-secret", default=None, help="Alpaca API secret (overrides ALPACA_API_SECRET env)")
    parser.add_argument("--alpaca-base", default=None, help="Alpaca base URL (overrides ALPACA_BASE_URL env)")
    parser.add_argument("--min-eps-surprise", type=float, default=5.0,
                         help="Minimum EPS beat percentage")
    parser.add_argument("--min-reaction", type=float, default=2.0,
                         help="Minimum next-day price reaction percentage")
    parser.add_argument("--min-volume", type=float, default=1_000_000,
                         help="Minimum average daily volume filter")
    parser.add_argument("--max-hold-days", type=int, default=60,
                         help="Maximum trading days to hold a position")
    parser.add_argument("--backtest", action="store_true",
                         help="Run backtest instead of live screener")
    parser.add_argument("--start", default="2022-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Backtest end date (YYYY-MM-DD), default today")
    parser.add_argument("--csv", default=None, help="Optional path to save screener results as CSV")
    args = parser.parse_args()

    # ensure outputs directory exists
    outputs_dir = os.getenv("OUTPUTS_DIR", "outputs")
    os.makedirs(outputs_dir, exist_ok=True)

    cfg = PEADConfig(
        min_eps_surprise_pct=args.min_eps_surprise,
        min_reaction_pct=args.min_reaction,
        min_avg_volume=args.min_volume,
        max_hold_days=args.max_hold_days,
    )

    # allow CLI to override provider and API keys/urls
    if args.data_provider:
        DATA_PROVIDER = args.data_provider.lower()
    # Twelve Data CLI override
    if args.td_api_key:
        globals()["TWELVEDATA_API_KEY"] = args.td_api_key
    # Alpaca CLI overrides
    if args.alpaca_key:
        globals()["ALPACA_API_KEY"] = args.alpaca_key
    if args.alpaca_secret:
        globals()["ALPACA_API_SECRET"] = args.alpaca_secret
    if args.alpaca_base:
        globals()["ALPACA_BASE_URL"] = args.alpaca_base
    # decide which providers to run: single if specified, otherwise run all implemented providers
    implemented_providers = ["yahoo", "twelvedata", "alpaca"]
    providers_to_run = [args.data_provider.lower()] if args.data_provider else implemented_providers

    if args.backtest:
        for provider in providers_to_run:
            # set provider for fetch functions
            old_provider = globals().get("DATA_PROVIDER")
            globals()["DATA_PROVIDER"] = provider
            print(f"\n=== Backtesting (provider={provider}) for tickers {args.tickers} ({args.start} to {args.end or 'today'}) ===")
            for ticker in args.tickers:
                try:
                    print(f"\n--- {provider} | {ticker} ---")
                    result = backtest_ticker(ticker, cfg, args.start, args.end)
                    print(pd.Series(result["summary"]))
                    if result["trades"]:
                        print("\nTrade log:")
                        print(pd.DataFrame(result["trades"]).to_string(index=False))
                except Exception as e:
                    print(f"  Error backtesting {ticker} with provider {provider}: {e}")

            # if user requested CSV, save with provider prefix/suffix into outputs folder
            if args.csv:
                csv_dir, csv_base = os.path.split(args.csv)
                provider_csv = os.path.join(csv_dir, f"{provider}_{csv_base}") if csv_dir else f"{provider}_{csv_base}"
                try:
                    # attempt to create a combined screen/backtest summary CSV if available
                    results_df = screen_watchlist(args.tickers, cfg)
                    if not results_df.empty:
                        results_df.to_csv(provider_csv, index=False)
                        print(f"Saved results to {provider_csv}")
                        # also save HTML with timestamp in name
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        base, _ = os.path.splitext(provider_csv)
                        provider_html = f"{base}_{ts}.html"
                        try:
                            html_content = f"<h2>Provider: {provider}</h2>\n<p>Generated: {ts}</p>\n" + results_df.to_html(index=False, border=0)
                            with open(provider_html, "w", encoding="utf-8") as hf:
                                hf.write(html_content)
                            print(f"Saved HTML report to {provider_html}")
                        except Exception as e:
                            print(f"  Could not write HTML for provider {provider}: {e}")
                except Exception as e:
                    print(f"  Could not save CSV for provider {provider}: {e}")

            # restore original provider
            if old_provider is not None:
                globals()["DATA_PROVIDER"] = old_provider
            else:
                globals().pop("DATA_PROVIDER", None)
            print(f"\n=== Backtesting {ticker} ({args.start} to {args.end or 'today'}) ===")
        # screening mode: run for each provider (or single) and optionally save per-provider CSV
        for provider in providers_to_run:
            old_provider = globals().get("DATA_PROVIDER")
            globals()["DATA_PROVIDER"] = provider
            print(f"\nScreening (provider={provider}) {len(args.tickers)} tickers for earnings-beat momentum signals...\n")
            try:
                results = screen_watchlist(args.tickers, cfg)
                if results.empty:
                    print("No data returned.")
                else:
                    pd.set_option("display.width", 160)
                    pd.set_option("display.max_columns", None)
                    print("\n" + results.to_string(index=False))

                    buys = results[results["SIGNAL"] == "BUY/HOLD"]
                    print(f"\n{len(buys)} ticker(s) currently meeting all entry criteria: "
                          f"{', '.join(buys['ticker'].tolist()) if not buys.empty else 'none'}")

                    if args.csv:
                        csv_dir, csv_base = os.path.split(args.csv)
                        provider_csv = os.path.join(csv_dir, f"{provider}_{csv_base}") if csv_dir else f"{provider}_{csv_base}"
                        results.to_csv(provider_csv, index=False)
                        print(f"\nSaved results to {provider_csv}")
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        base, _ = os.path.splitext(provider_csv)
                        provider_html = f"{base}_{ts}.html"
                        try:
                            html_content = f"<h2>Provider: {provider}</h2>\n<p>Generated: {ts}</p>\n" + results.to_html(index=False, border=0)
                            with open(provider_html, "w", encoding="utf-8") as hf:
                                hf.write(html_content)
                            print(f"Saved HTML report to {provider_html}")
                        except Exception as e:
                            print(f"  Could not write HTML for provider {provider}: {e}")
            except Exception as e:
                print(f"  Error screening with provider {provider}: {e}")

            if old_provider is not None:
                globals()["DATA_PROVIDER"] = old_provider
            else:
                globals().pop("DATA_PROVIDER", None)
    else:
        print(f"\nScreening {len(args.tickers)} tickers for earnings-beat momentum signals...\n")
        results = screen_watchlist(args.tickers, cfg)
        if results.empty:
            print("No data returned.")
            return
        pd.set_option("display.width", 160)
        pd.set_option("display.max_columns", None)
        print("\n" + results.to_string(index=False))

        buys = results[results["SIGNAL"] == "BUY/HOLD"]
        print(f"\n{len(buys)} ticker(s) currently meeting all entry criteria: "
              f"{', '.join(buys['ticker'].tolist()) if not buys.empty else 'none'}")

        if args.csv:
            out_csv = args.csv
            results.to_csv(out_csv, index=False)
            print(f"\nSaved results to {out_csv}")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base, _ = os.path.splitext(out_csv)
            out_html = f"{base}_{ts}.html"
            try:
                html_content = f"<h2>Results</h2>\n<p>Generated: {ts}</p>\n" + results.to_html(index=False, border=0)
                with open(out_html, "w", encoding="utf-8") as hf:
                    hf.write(html_content)
                print(f"Saved HTML report to {out_html}")
            except Exception as e:
                print(f"  Could not write HTML: {e}")


if __name__ == "__main__":
    main()