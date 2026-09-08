"""
breadth_monitor.py  —  Mighty7under StockBee-style Market Monitor
=================================================================
Counts how much of the US stock universe is doing each thing today, appends
the counts to a per-day history, and writes the table the Breadth tab shows.

  data/breadth_history.json  — append-only {date: {metric: count}}
  data/breadth_monitor.json  — the last N days, with ratios, for the UI

Every metric is a single TradingView scanner call that reads only
``totalCount`` — no rows are transferred, so the whole run is ~15 tiny
requests.

Note on history: the scanner is point-in-time, so "stocks up 4% today" cannot
be recovered for past dates. The 5- and 10-day ratios are therefore computed
from days this script has actually recorded, and only appear once enough days
have accumulated. Ratios are None until then rather than being faked from a
short window.

Usage:
  python scripts/breadth_monitor.py [--out-dir data] [--days 30]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

SCAN_URL = "https://scanner.tradingview.com/america/scan?label-product=screener-stock"
EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]

BASE = [
    {"left": "type", "operation": "equal", "right": "stock"},
    {"left": "exchange", "operation": "in_range", "right": EXCHANGES},
]

# Each metric is (key, extra filters). The universe itself is just BASE.
METRICS = [
    # ── Primary: daily movers ────────────────────────────────────────────────
    ("up4",        [{"left": "change", "operation": "greater", "right": 4}]),
    ("down4",      [{"left": "change", "operation": "less", "right": -4}]),
    # ── Secondary: trend windows ─────────────────────────────────────────────
    ("up25q",      [{"left": "Perf.3M", "operation": "greater", "right": 25}]),
    ("down25q",    [{"left": "Perf.3M", "operation": "less", "right": -25}]),
    ("up25m",      [{"left": "Perf.1M", "operation": "greater", "right": 25}]),
    ("down25m",    [{"left": "Perf.1M", "operation": "less", "right": -25}]),
    ("up50m",      [{"left": "Perf.1M", "operation": "greater", "right": 50}]),
    ("down50m",    [{"left": "Perf.1M", "operation": "less", "right": -50}]),
    # StockBee uses a 34-day window here; TradingView exposes no 34-day
    # performance field, so this is the 1-month (~21 session) equivalent and is
    # labelled "Month" in the UI rather than pretending to be 34 days.
    ("up13m",      [{"left": "Perf.1M", "operation": "greater", "right": 13}]),
    ("down13m",    [{"left": "Perf.1M", "operation": "less", "right": -13}]),
    # ── Context ──────────────────────────────────────────────────────────────
    # T2108 numerator: price above its 40-day SMA (the classic definition).
    ("above40sma", [{"left": "close", "operation": "greater", "right": "SMA40"}]),
]

ATR_MULT = 10.0


def count(session: requests.Session, filters: list) -> int:
    """Run a scanner query and return only the match count."""
    payload = {
        "markets": ["america"],
        "filter": filters,
        "options": {"lang": "en"},
        "symbols": {"query": {"types": ["stock"]}, "tickers": []},
        "columns": ["name"],
        "range": [0, 1],
    }
    r = session.post(SCAN_URL, json=payload, timeout=30)
    r.raise_for_status()
    return int(r.json().get("totalCount", 0))


def atr_extension_count(session: requests.Session, mult: float = ATR_MULT) -> int:
    """
    Stocks trading more than `mult` ATRs above their 50-day SMA.

    The filter language cannot express ``close - SMA50 > mult * ATR``, so pull
    the three columns for the whole universe and count locally — it is a single
    request and the universe is only a few thousand rows.
    """
    payload = {
        "markets": ["america"],
        "filter": BASE,
        "options": {"lang": "en"},
        "symbols": {"query": {"types": ["stock"]}, "tickers": []},
        "columns": ["close", "SMA50", "ATR"],
        "range": [0, 20000],
    }
    r = session.post(SCAN_URL, json=payload, timeout=60)
    r.raise_for_status()
    n = 0
    for row in r.json().get("data") or []:
        d = row.get("d") or []
        if len(d) < 3:
            continue
        close, sma50, atr = d[0], d[1], d[2]
        if close is None or sma50 is None or not atr:
            continue
        if (close - sma50) > mult * atr:
            n += 1
    return n


def ratio(history: dict, dates: list, up_key: str, down_key: str, window: int):
    """
    StockBee ratio: summed up-moves over summed down-moves across `window`
    sessions. None until that many recorded days exist.
    """
    if len(dates) < window:
        return None
    up = sum(history[d].get(up_key) or 0 for d in dates[:window])
    down = sum(history[d].get(down_key) or 0 for d in dates[:window])
    if down == 0:
        return None if up == 0 else 99.0
    return round(up / down, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--days", type=int, default=30, help="rows to publish")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mighty7under/1.0"})

    print("📊 Counting market breadth…")
    today = {}
    try:
        today["universe"] = count(session, BASE)
        print(f"  ✓ universe: {today['universe']}")
    except Exception as e:
        print(f"  ✗ universe: {type(e).__name__}: {e}", file=sys.stderr)
        print("✗ Could not size the universe — keeping previous file", file=sys.stderr)
        sys.exit(1)

    for key, extra in METRICS:
        try:
            today[key] = count(session, BASE + extra)
            print(f"  ✓ {key}: {today[key]}")
        except Exception as e:
            today[key] = None
            print(f"  ✗ {key}: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        today["atr10x"] = atr_extension_count(session)
        print(f"  ✓ atr10x: {today['atr10x']}")
    except Exception as e:
        today["atr10x"] = None
        print(f"  ✗ atr10x: {type(e).__name__}: {e}", file=sys.stderr)

    # T2108 — percent of the universe above its 40-day SMA.
    if today.get("above40sma") is not None and today["universe"]:
        today["t2108"] = round(today["above40sma"] / today["universe"] * 100, 2)
    else:
        today["t2108"] = None

    # ── Append to history ────────────────────────────────────────────────────
    hist_path = os.path.join(args.out_dir, "breadth_history.json")
    history = {}
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠  history unreadable, starting fresh: {e}", file=sys.stderr)

    day = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    history[day] = today          # a second run the same day overwrites it
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, sort_keys=True)

    # ── Build the published table (newest first) ─────────────────────────────
    dates = sorted(history.keys(), reverse=True)
    rows = []
    for i, d in enumerate(dates[:args.days]):
        rec = dict(history[d])
        rec["date"] = d
        # Ratios look back from this row, so older rows keep their own context.
        rec["ratio5"] = ratio(history, dates[i:], "up4", "down4", 5)
        rec["ratio10"] = ratio(history, dates[i:], "up4", "down4", 10)
        rows.append(rec)

    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "updated_label": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z"),
        "source": "TradingView Screener",
        "days_recorded": len(history),
        "rows": rows,
    }
    with open(os.path.join(args.out_dir, "breadth_monitor.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"✅ {day}: T2108 {today['t2108']}% · up4 {today.get('up4')} / "
          f"down4 {today.get('down4')} · {len(history)} day(s) recorded")
    if len(history) < 10:
        print(f"   ({10 - len(history)} more session(s) until the 10-day ratio fills in)")


if __name__ == "__main__":
    main()
