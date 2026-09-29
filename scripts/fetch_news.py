"""
fetch_news.py  —  Mighty7under Finviz Catalyst News
===================================================
Scrapes finviz via the finvizfinance library and writes:
  data/news.json    — market headlines + per-ticker catalyst news

It used to write data/events.json too, but finviz retired its economic
calendar: calendar.ashx now serves an EARNINGS calendar (ticker, epsEstimate,
salesEstimate), whose rows share only the `date` field with what
finvizfinance.calendar expects. That produced correctly-dated events with
blank names, so the calendar half was removed and the Calendar tab now uses
the live TradingView economic-calendar embed instead.

The ticker list is taken from data/scans.json: the names your scans caught,
most-hit first, so the catalyst feed explains the moves you are actually
looking at.

finviz sits behind Cloudflare and blocks by IP reputation. finvizfinance
raises FinvizBlockedError for that; this script treats any total failure as
"leave the previous file alone" so a block never publishes an empty board.

Usage:
  python scripts/fetch_news.py [--out-dir data] [--tickers 12] [--per-ticker 6]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from finvizfinance.news import News
from finvizfinance.quote import finvizfinance


def clean(v):
    """finviz uses '-' and '' for absent values."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "-", "nan", "None") else s


def frame_rows(df: pd.DataFrame, n: int) -> list:
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.head(n).iterrows():
        title = clean(r.get("Title"))
        link = clean(r.get("Link"))
        if not title or not link:
            continue        # finviz lazy-loads some rows as empty placeholders
        if link.startswith("/"):
            link = "https://finviz.com" + link
        date = r.get("Date")
        if isinstance(date, pd.Timestamp):
            date = date.strftime("%Y-%m-%d %H:%M")
        out.append({
            "date":   clean(date),
            "title":  title,
            "source": clean(r.get("Source")),
            "link":   link,
        })
    return out


def scan_tickers(out_dir: str, limit: int) -> list:
    """Most-hit tickers from the latest scan run — the moves worth explaining."""
    path = os.path.join(out_dir, "scans.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f).get("rows", [])
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠  scans.json unreadable: {e}", file=sys.stderr)
        return []
    return [r["ticker"] for r in rows[:limit] if r.get("ticker")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--tickers", type=int, default=12,
                    help="how many scan tickers to fetch catalyst news for")
    ap.add_argument("--per-ticker", type=int, default=6)
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between finviz requests")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    ok = False

    # ── News ─────────────────────────────────────────────────────────────────
    news = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "updated_label": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z"),
        "market": [],
        "tickers": {},
    }

    print("📰 Fetching market headlines…")
    try:
        news["market"] = frame_rows(News().get_news().get("news"), 25)
        print(f"  ✓ {len(news['market'])} headlines")
        ok = True
    except Exception as e:
        print(f"  ✗ market news: {type(e).__name__}: {e}", file=sys.stderr)

    tickers = scan_tickers(args.out_dir, args.tickers)
    if tickers:
        print(f"🔎 Fetching catalyst news for {len(tickers)} scan tickers…")
        for i, t in enumerate(tickers):
            try:
                rows = frame_rows(finvizfinance(t).ticker_news(), args.per_ticker)
                if rows:
                    news["tickers"][t] = rows
                print(f"  ✓ {t}: {len(rows)}")
                ok = True
            except Exception as e:
                print(f"  ✗ {t}: {type(e).__name__}: {e}", file=sys.stderr)
            if i < len(tickers) - 1:
                time.sleep(args.delay)     # be a polite scraper
    else:
        print("  ⚠  no scans.json tickers yet — skipping catalyst news")

    if not ok:
        # Everything failed (almost certainly a Cloudflare block). Leave the
        # previous news.json in place.
        print("✗ All finviz fetches failed — keeping previous files", file=sys.stderr)
        sys.exit(1)

    with open(os.path.join(args.out_dir, "news.json"), "w", encoding="utf-8") as f:
        json.dump(news, f, indent=2)
    print(f"✅ news.json: {len(news['market'])} headlines, "
          f"{len(news['tickers'])} tickers with catalysts")


if __name__ == "__main__":
    main()
