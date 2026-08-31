"""
run_backtests.py

Run `EarningBeatMovmtm.py` backtests sequentially for multiple data providers
and save CSV + log per provider. Intended for on-demand runs.

Usage examples:
    python run_backtests.py --providers yahoo twelvedata alpaca --tickers AAPL --start 2023-01-01

Outputs are saved to `outputs/{provider}_results.csv` and `outputs/{provider}.log`.
"""

import argparse
import os
import subprocess
from datetime import datetime
import pandas as pd
import html
from pathlib import Path

DEFAULT_PROVIDERS = ["yahoo", "twelvedata", "alpaca"]


def main():
    parser = argparse.ArgumentParser(description="Run backtests across multiple data providers")
    parser.add_argument("--providers", nargs="+", default=DEFAULT_PROVIDERS,
                        help="List of providers to run (yahoo, twelvedata, alpaca)")
    parser.add_argument("--tickers", nargs="+", default=None, help="Tickers to backtest (default watchlist)")
    parser.add_argument("--start", default="2023-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--outdir", default="outputs", help="Directory to save CSVs and logs")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    # if tickers not provided, import default watchlist from the module
    if args.tickers is None:
        try:
            import EarningBeatMovmtm as ebm
            tickers = ebm.DEFAULT_WATCHLIST
        except Exception:
            tickers = ["AAPL"]
    else:
        tickers = args.tickers

    python_exe = os.environ.get("PYTHON", "python")

    results = []
    combined_parts = []
    for provider in args.providers:
        env = os.environ.copy()
        env["DATA_PROVIDER"] = provider
        out_csv = os.path.join(args.outdir, f"results_{provider}.csv")
        log_file = os.path.join(args.outdir, f"run_{provider}.log")

        cmd = [python_exe, "EarningBeatMovmtm.py", "--backtest", "--start", args.start]
        if args.end:
            cmd += ["--end", args.end]
        cmd += ["--tickers"] + tickers
        cmd += ["--csv", out_csv]

        print(f"Running provider={provider} -> {out_csv}")
        with open(log_file, "w", encoding="utf-8") as lf:
            proc = subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT)
        results.append((provider, proc.returncode, out_csv, log_file))
        # build per-provider HTML report
        part_lines = []
        part_lines.append(f"<h2>Provider: {html.escape(provider)}</h2>")
        part_lines.append(f"<p>Return code: {proc.returncode}</p>")

        # embed CSV data if present
        if os.path.exists(out_csv):
            try:
                df = pd.read_csv(out_csv)
                table_html = df.to_html(index=False, classes="dataframe", border=0)
                part_lines.append("<h3>Screener / Backtest Results</h3>")
                part_lines.append(table_html)
            except Exception as e:
                part_lines.append(f"<p><em>Could not read CSV: {html.escape(str(e))}</em></p>")
        else:
            part_lines.append("<p><em>No CSV output produced.</em></p>")

        # include log file
        if os.path.exists(log_file):
            try:
                log_text = Path(log_file).read_text(encoding="utf-8")
                part_lines.append("<h3>Log</h3>")
                part_lines.append(f"<pre>{html.escape(log_text)}</pre>")
            except Exception as e:
                part_lines.append(f"<p><em>Could not read log: {html.escape(str(e))}</em></p>")

        provider_html = "\n".join(part_lines)
        # write individual provider HTML
        provider_html_file = os.path.join(args.outdir, f"report_{provider}.html")
        with open(provider_html_file, "w", encoding="utf-8") as f:
            f.write(provider_html)

        combined_parts.append(provider_html)

    print("\nSummary:")
    for provider, rc, out_csv, log_file in results:
        status = "OK" if rc == 0 else f"FAIL({rc})"
        print(f"{provider}: {status}, csv={out_csv}, log={log_file}")

    # build combined HTML
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    combined_html = [
        "<html><head><meta charset=\"utf-8\"><title>Backtest Reports</title>",
        "<style>body{font-family:Arial,Helvetica,sans-serif;margin:20px} .dataframe{border-collapse:collapse} table.dataframe th, table.dataframe td{border:1px solid #ddd;padding:6px}</style>",
        "</head><body>",
        f"<h1>Combined Backtest Reports - {timestamp}</h1>",
    ]
    combined_html.extend(combined_parts)
    combined_html.append("</body></html>")

    combined_file = os.path.join(args.outdir, f"combined_reports_{timestamp}.html")
    with open(combined_file, "w", encoding="utf-8") as f:
        f.write("\n".join(combined_html))

    print(f"\nCombined HTML written to: {combined_file}")


if __name__ == "__main__":
    main()
