"""
breadth_backfill.py  —  Backfill the Market Monitor from price history
======================================================================
breadth_monitor.py can only record what the scanner reports *today*, so the
monitor starts life with a single row and no 5/10-day ratios. Every metric in
it, though, is derivable from daily bars — so this script reconstructs the
missing sessions from price history and fills the gaps in
data/breadth_history.json.

Rules:
  * Days already recorded live are NEVER overwritten. Live rows come from the
    real scanner over the full universe and are authoritative; backfilled rows
    are marked ``"backfilled": true`` so the UI can flag them.
  * The universe is whatever trades today, so backfilled rows carry
    survivorship bias — delisted names are absent, and each historical row
    counts only tickers that already had data on that date.

Run once after deploying the monitor (or whenever you want deeper history):
  python scripts/breadth_backfill.py [--out-dir data] [--years 1] [--limit 0]
"""

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from breadth_monitor import BASE, SCAN_URL, ATR_MULT

ATR_LEN = 14          # TradingView's default ATR length


def load_universe(limit: int) -> list:
    payload = {
        "markets": ["america"],
        "filter": BASE,
        "options": {"lang": "en"},
        "symbols": {"query": {"types": ["stock"]}, "tickers": []},
        "columns": ["name"],
        "range": [0, limit if limit else 20000],
    }
    r = requests.post(SCAN_URL, json=payload, timeout=60)
    r.raise_for_status()
    out = []
    for item in r.json().get("data") or []:
        d = item.get("d") or []
        if d and isinstance(d[0], str):
            out.append(d[0])
    return out


def download(tickers: list, years: int, chunk: int = 200):
    closes, highs, lows = [], [], []
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        try:
            raw = yf.download(batch, period=f"{years}y", interval="1d",
                              auto_adjust=True, progress=False, threads=True)
        except Exception as e:
            print(f"  ⚠  batch {i // chunk}: {e}", file=sys.stderr)
            continue
        if raw is None or raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            closes.append(raw["Close"]); highs.append(raw["High"]); lows.append(raw["Low"])
        else:
            closes.append(raw[["Close"]].rename(columns={"Close": batch[0]}))
            highs.append(raw[["High"]].rename(columns={"High": batch[0]}))
            lows.append(raw[["Low"]].rename(columns={"Low": batch[0]}))
        print(f"  ✓ {min(i + chunk, len(tickers))}/{len(tickers)}")
    if not closes:
        return None, None, None
    return (pd.concat(closes, axis=1), pd.concat(highs, axis=1), pd.concat(lows, axis=1))


def compute_rows(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame) -> dict:
    """Every Market Monitor metric, per session, as {date: {metric: count}}."""
    chg = close.pct_change(1, fill_method=None) * 100
    p21 = close.pct_change(21, fill_method=None) * 100
    p63 = close.pct_change(63, fill_method=None) * 100
    sma40 = close.rolling(40, min_periods=40).mean()
    sma50 = close.rolling(50, min_periods=50).mean()

    prev = close.shift(1)
    tr = np.maximum(np.maximum((high - low).abs(), (high - prev).abs()),
                    (low - prev).abs())            # element-wise true range
    atr = tr.rolling(ATR_LEN, min_periods=ATR_LEN).mean()

    have = close.notna()
    counts = {
        "universe":   have.sum(axis=1),
        "up4":        (chg > 4).sum(axis=1),
        "down4":      (chg < -4).sum(axis=1),
        "up25q":      (p63 > 25).sum(axis=1),
        "down25q":    (p63 < -25).sum(axis=1),
        "up25m":      (p21 > 25).sum(axis=1),
        "down25m":    (p21 < -25).sum(axis=1),
        "up50m":      (p21 > 50).sum(axis=1),
        "down50m":    (p21 < -50).sum(axis=1),
        "up13m":      (p21 > 13).sum(axis=1),
        "down13m":    (p21 < -13).sum(axis=1),
        "above40sma": (close > sma40).sum(axis=1),
        "atr10x":     ((close - sma50) > ATR_MULT * atr).sum(axis=1),
    }

    frame = pd.DataFrame(counts)
    # Drop the warm-up window first (3-month returns and the 50-day SMA are NaN
    # there, so those columns would read as zero), then require a real universe.
    frame = frame.iloc[63:]
    frame = frame[frame["universe"] > 100]

    rows = {}
    for idx, r in frame.iterrows():
        uni = int(r["universe"])
        rec = {k: int(v) for k, v in r.items()}
        rec["t2108"] = round(rec["above40sma"] / uni * 100, 2) if uni else None
        rec["backfilled"] = True
        rows[idx.strftime("%Y-%m-%d")] = rec
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--years", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the universe (0 = all, matches the live monitor)")
    args = ap.parse_args()

    print("🌐 Loading universe…")
    tickers = load_universe(args.limit)
    if not tickers:
        print("✗ Empty universe", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ {len(tickers)} tickers")

    print(f"📉 Downloading {args.years}y of daily bars (this is the slow part)…")
    close, high, low = download(tickers, args.years)
    if close is None or close.empty:
        print("✗ No price data", file=sys.stderr)
        sys.exit(1)
    close = close.dropna(how="all")
    high = high.reindex(index=close.index, columns=close.columns)
    low = low.reindex(index=close.index, columns=close.columns)
    print(f"  ✓ {close.shape[1]} tickers × {close.shape[0]} sessions")

    print("🧮 Reconstructing sessions…")
    recon = compute_rows(close, high, low)
    print(f"  ✓ {len(recon)} sessions reconstructed")

    hist_path = os.path.join(args.out_dir, "breadth_history.json")
    history = {}
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠  history unreadable: {e}", file=sys.stderr)

    live_days = {d for d, r in history.items() if not r.get("backfilled")}
    added = 0
    for day, rec in recon.items():
        if day in live_days:
            continue          # never clobber a real recorded session
        if day not in history or history[day].get("backfilled"):
            history[day] = rec
            added += 1

    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, sort_keys=True)

    print(f"✅ {added} session(s) added · {len(history)} total "
          f"({len(live_days)} live, {len(history) - len(live_days)} backfilled)")
    print("   Re-run scripts/breadth_monitor.py to republish breadth_monitor.json")


if __name__ == "__main__":
    main()
