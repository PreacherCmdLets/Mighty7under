"""
fetch_calendar.py  —  Mighty7under Economic Calendar
====================================================
Writes data/events.json: upcoming US economic releases with actual, consensus
and previous values.

Why Nasdaq and not finviz or TradingView:
  * finviz retired its economic calendar — calendar.ashx now serves an
    EARNINGS calendar (ticker / epsEstimate / salesEstimate). Its rows share
    only the `date` field with what finvizfinance.calendar expects, which
    produced correctly-dated events with blank names.
  * TradingView's economic-calendar widget script now returns 403; every
    other embed-widget-*.js still returns 200, so that widget is retired too.
  * Nasdaq's public calendar endpoint returns clean JSON per day and needs no
    key: gmt, country, eventName, actual, consensus, previous, description.

Two quirks in Nasdaq's response, both verified against releases whose weekday
is fixed, and both corrected here:

  * The `date` query parameter runs ONE DAY LATE. Asking for 2026-10-03 returns
    Nonfarm Payrolls (always a Friday; the real date was 2026-10-02), asking for
    2026-10-02 returns Initial Jobless Claims (always a Thursday) and the ISM
    Manufacturing PMI (first business day, 2026-10-01). So a release dated T is
    fetched by requesting T+1. If Nasdaq ever fixes this the calendar will shift
    a day early — the check is simply whether Nonfarm Payrolls lands on a Friday.
  * The `gmt` field is NOT GMT. Nonfarm Payrolls reads 08:30 and the ISM PMI
    10:00, which are their Eastern release times. Labelled ET accordingly.

Nasdaq carries no importance field, so `impact` here is DERIVED from the event
name against the list below — it is our classification, not Nasdaq's.

Usage:
  python scripts/fetch_calendar.py [--out-dir data] [--days 10]
"""

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

API = "https://api.nasdaq.com/api/calendar/economicevents"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"),
    "Accept": "application/json",
}

# Derived impact: these are the releases that actually move a US session.
HIGH = ("fomc", "interest rate", "nonfarm payroll", "cpi", "consumer price",
        "ppi", "producer price", "gdp", "pce", "unemployment rate", "powell")
MEDIUM = ("jobless claims", "retail sales", "ism", "consumer confidence",
          "durable goods", "housing starts", "industrial production",
          "michigan", "trade balance", "factory orders", "adp", "jolts",
          "building permits", "existing home", "new home sales", "beige book")


def clean(v):
    """Nasdaq pads empty cells with &nbsp; and stray whitespace."""
    if v is None:
        return None
    s = html.unescape(str(v)).replace("\xa0", " ").strip()
    return s or None


def impact_of(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in HIGH):
        return "high"
    if any(k in n for k in MEDIUM):
        return "medium"
    return "low"


def fetch_day(session: requests.Session, day: str) -> list:
    r = session.get(API, params={"date": day}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = (r.json() or {}).get("data") or {}
    return data.get("rows") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--days", type=int, default=10, help="calendar days ahead")
    ap.add_argument("--country", default="United States")
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    session = requests.Session()
    today = datetime.now(ZoneInfo("America/New_York")).date()

    print(f"📅 Fetching {args.days} days of economic events…")
    events, failures = [], 0
    for i in range(args.days):
        release = today + timedelta(days=i)          # the date we want
        query = (release + timedelta(days=1)).isoformat()   # Nasdaq runs a day late
        day = release.isoformat()
        try:
            rows = fetch_day(session, query)
        except Exception as e:
            failures += 1
            print(f"  ✗ {day}: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        kept = 0
        for r in rows:
            if args.country and clean(r.get("country")) != args.country:
                continue
            name = clean(r.get("eventName"))
            if not name:
                continue
            gmt = clean(r.get("gmt")) or ""
            # Field is named `gmt` but carries Eastern times (see module docstring).
            events.append({
                "date":     day,
                "time":     f"{gmt} ET" if re.match(r"^\d{1,2}:\d{2}$", gmt) else (gmt or "—"),
                "event":    name,
                "impact":   impact_of(name),
                "country":  "US",
                "actual":   clean(r.get("actual")),
                "expected": clean(r.get("consensus")),
                "prior":    clean(r.get("previous")),
            })
            kept += 1
        print(f"  ✓ {day}: {kept}")
        if i < args.days - 1:
            time.sleep(args.delay)

    if not events:
        print("✗ No events fetched — keeping the previous events.json", file=sys.stderr)
        sys.exit(1)

    events.sort(key=lambda e: (e["date"], e["time"]))
    path = os.path.join(args.out_dir, "events.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2)

    hi = sum(1 for e in events if e["impact"] == "high")
    med = sum(1 for e in events if e["impact"] == "medium")
    print(f"✅ {len(events)} events ({hi} high, {med} medium) → {path}")
    if failures:
        print(f"⚠  {failures} day(s) failed")


if __name__ == "__main__":
    main()
