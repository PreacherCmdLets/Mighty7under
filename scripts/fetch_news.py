"""
fetch_news.py  —  Mighty7under Finviz Calendar & Catalyst News
==============================================================
Scrapes finviz via the finvizfinance library and writes:
  data/events.json  — upcoming US economic calendar (Key Events table)
  data/news.json    — market headlines + per-ticker catalyst news

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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from finvizfinance.calendar import Calendar
from finvizfinance.news import News
from finvizfinance.quote import finvizfinance

# finviz importance 3/2/1 -> the impact levels the Key Events table styles.
IMPACT = {"3": "high", "2": "medium", "1": "low"}

# "Tue Sep 08, 08:15 AM" / "Mon Sep 07"
DT_FORMATS = ("%a %b %d, %I:%M %p", "%a %b %d")


def clean(v):
    """finviz uses '-' and '' for absent values."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "-", "nan", "None") else s


def parse_when(text: str, today: datetime):
    """
    Turn finviz's "Tue Sep 08, 08:15 AM" into (iso_date, time_label).

    The string carries no year, so assume the calendar's own window: anything
    more than ~6 months behind today has rolled into next year.
    """
    text = (text or "").strip()
    for fmt in DT_FORMATS:
        try:
            dt = datetime.strptime(text, fmt).replace(year=today.year)
        except ValueError:
            continue
        if dt.date() < today.date() - timedelta(days=180):
            dt = dt.replace(year=today.year + 1)
        has_time = fmt == DT_FORMATS[0]
        return dt.strftime("%Y-%m-%d"), (dt.strftime("%I:%M %p").lstrip("0") if has_time else "All day")
    return None, None


def build_events(limit: int = 40) -> list:
    """Upcoming US economic releases, today onward, highest impact first."""
    df = Calendar().calendar()
    if df is None or df.empty:
        return []

    today = datetime.now(ZoneInfo("America/New_York"))
    events = []
    for _, r in df.iterrows():
        iso, tlabel = parse_when(r.get("Datetime"), today)
        if iso is None or iso < today.strftime("%Y-%m-%d"):
            continue        # drop already-released rows
        impact = IMPACT.get(clean(r.get("Impact")) or "", "low")
        events.append({
            "date":     iso,
            "time":     tlabel,
            "event":    clean(r.get("Release")) or "—",
            "impact":   impact,
            "country":  "US",
            "for":      clean(r.get("For")),
            "actual":   clean(r.get("Actual")),
            "expected": clean(r.get("Expected")),
            "prior":    clean(r.get("Prior")),
        })

    events.sort(key=lambda e: (e["date"], e["time"]))
    return events[:limit]


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

    # ── Economic calendar ────────────────────────────────────────────────────
    print("📅 Fetching finviz economic calendar…")
    try:
        events = build_events()
        with open(os.path.join(args.out_dir, "events.json"), "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2)
        high = sum(1 for e in events if e["impact"] == "high")
        print(f"  ✓ {len(events)} upcoming events ({high} high impact)")
        ok = True
    except Exception as e:
        print(f"  ✗ calendar: {type(e).__name__}: {e}", file=sys.stderr)

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
        # previous events.json / news.json in place.
        print("✗ All finviz fetches failed — keeping previous files", file=sys.stderr)
        sys.exit(1)

    with open(os.path.join(args.out_dir, "news.json"), "w", encoding="utf-8") as f:
        json.dump(news, f, indent=2)
    print(f"✅ news.json: {len(news['market'])} headlines, "
          f"{len(news['tickers'])} tickers with catalysts")


if __name__ == "__main__":
    main()
