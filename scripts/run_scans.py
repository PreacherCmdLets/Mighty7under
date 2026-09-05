"""
run_scans.py  —  Mighty7under TradingView Scan Runner
=====================================================
Runs the TradingView screener scans (refactored from SUNCODE.ipynb) and writes:
  data/scans.json                  — deduped results, one row per ticker
  data/scan_history/YYYY-MM.json   — append-only daily ticker lists (for Tracker)

Adding a scan = adding one entry to SCANS below. Every scan shares the base
filters (US common stock on NASDAQ/NYSE/AMEX); anything else is optional keys:

  mcap            (lo, hi)  market cap band; hi may be None
  min_avg_vol     average_volume_60d_calc floor
  min_vol         volume floor
  max_float       float_shares_outstanding cap
  min_volatility  Volatility.M floor
  perf            (field, threshold) e.g. ("Perf.3M", 70) — greater than
  extra           callable returning a list of additional col() conditions
  sort_by         column to rank by before limit() (defaults to the perf field)
  post            pandas-side filters the screener cannot express:
                    low_mult        close >= price_52_week_low * low_mult
                    ma              (name, band) — MA sits at or below close,
                                    but no further than close * band
                    sma10_gt_sma20  require SMA10 > SMA20

Usage:
  python scripts/run_scans.py [--out-dir data] [--limit 300]
"""

import argparse
import concurrent.futures
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from tradingview_screener import Query, col

# ── Columns ───────────────────────────────────────────────────────────────────

# Pulled for every scan so the dashboard table is consistent.
DISPLAY_COLS = [
    "name", "description", "close", "change", "volume",
    "market_cap_basic", "float_shares_outstanding",
    "Volatility.M", "relative_volume_10d_calc",
]

# Extra columns required by post-filters / perf thresholds are added per scan.
POST_COLS = {"low_mult": ["price_52_week_low"], "sma10_gt_sma20": ["SMA10", "SMA20"]}

EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]

# ── Scan catalogue ────────────────────────────────────────────────────────────

_MOM_SMALL = dict(mcap=(300e6, 10e9), min_avg_vol=300_000, min_vol=100_000,
                  max_float=50e6, min_volatility=3,
                  post={"low_mult": 1.50, "ma": ("SMA10", 0.80)})
_MOM_LARGE = dict(mcap=(10e9, None), min_avg_vol=300_000, min_vol=100_000,
                  max_float=150e6,
                  post={"low_mult": 1.50, "ma": ("SMA10", 0.90)})

SCANS = [
    # ── Momentum: $300M – $10B ───────────────────────────────────────────────
    {"id": "mom_1w_small", "label": "Momentum 1W", "group": "Momentum",
     "mcap_group": "$300M – $10B", "timeframe": "1 Week",
     "perf": ("Perf.W", 20), **_MOM_SMALL},
    {"id": "mom_1m_small", "label": "Momentum 1M", "group": "Momentum",
     "mcap_group": "$300M – $10B", "timeframe": "1 Month",
     "perf": ("Perf.1M", 30), **_MOM_SMALL},
    {"id": "mom_3m_small", "label": "Momentum 3M", "group": "Momentum",
     "mcap_group": "$300M – $10B", "timeframe": "3 Months",
     "perf": ("Perf.3M", 70), **_MOM_SMALL},
    {"id": "mom_6m_small", "label": "Momentum 6M", "group": "Momentum",
     "mcap_group": "$300M – $10B", "timeframe": "6 Months",
     "perf": ("Perf.6M", 100), **_MOM_SMALL},

    # ── Momentum: > $10B ─────────────────────────────────────────────────────
    {"id": "mom_1w_large", "label": "Momentum 1W", "group": "Momentum",
     "mcap_group": "> $10B", "timeframe": "1 Week",
     "perf": ("Perf.W", 20), **_MOM_LARGE},
    {"id": "mom_1m_large", "label": "Momentum 1M", "group": "Momentum",
     "mcap_group": "> $10B", "timeframe": "1 Month",
     "perf": ("Perf.1M", 30), **_MOM_LARGE},
    {"id": "mom_3m_large", "label": "Momentum 3M", "group": "Momentum",
     "mcap_group": "> $10B", "timeframe": "3 Months",
     "perf": ("Perf.3M", 70), **_MOM_LARGE},
    {"id": "mom_6m_large", "label": "Momentum 6M", "group": "Momentum",
     "mcap_group": "> $10B", "timeframe": "6 Months",
     "perf": ("Perf.6M", 100), **_MOM_LARGE},

    # ── Growth & tightness ───────────────────────────────────────────────────
    {"id": "fundamental_growth", "label": "Fundamental Growth",
     "group": "Growth & Tightness",
     "mcap": (300e6, None), "min_avg_vol": 300_000, "max_float": 100e6,
     "extra": lambda: [
         col("earnings_per_share_diluted_yoy_growth_fq") > 25,
         col("free_cash_flow_yoy_growth_ttm") > 25,
         col("total_revenue_yoy_growth_fq") > 25,
     ]},
    {"id": "post_earnings_base", "label": "Post-Earnings Cont. Base",
     "group": "Growth & Tightness",
     "mcap": (50e6, None), "min_avg_vol": 250_000, "max_float": 50e6,
     "extra": lambda: [
         col("close") > col("SMA20"),
         col("relative_volume_10d_calc") >= 2,
         col("gap") > 5,
     ]},
    {"id": "strongest_jk", "label": "Strongest Stock (JK)",
     "group": "Growth & Tightness",
     "mcap": (300e6, 10e9), "min_avg_vol": 500_000, "max_float": 50e6,
     "min_volatility": 3,
     "extra": lambda: [
         col("earnings_per_share_diluted_yoy_growth_fq") > 25,
         col("total_revenue_yoy_growth_fq") > 25,
         col("close") > col("SMA50"),
     ],
     "post": {"low_mult": 1.70, "ma": ("SMA10", 0.90)}},
    {"id": "strongest_10b_jk", "label": "Strongest Stock 10B Rev 30 (JK)",
     "group": "Growth & Tightness",
     "mcap": (10e9, None), "min_avg_vol": 500_000, "max_float": 150e6,
     "min_volatility": 2,
     "extra": lambda: [
         col("earnings_per_share_diluted_yoy_growth_fq") > 25,
         col("total_revenue_yoy_growth_fq") > 25,
         col("close") > col("SMA50"),
     ],
     "post": {"low_mult": 1.70, "ma": ("SMA10", 0.97)}},
    {"id": "daily_tightness", "label": "Daily Tightness Swing",
     "group": "Growth & Tightness",
     "mcap": (300e6, None), "min_avg_vol": 300_000, "min_vol": 100_000,
     "max_float": 50e6, "min_volatility": 3.5,
     "extra": lambda: [col("Perf.W") < 5],
     "post": {"low_mult": 1.50, "ma": ("EMA5", 0.97), "sma10_gt_sma20": True}},
]

# ── Query building ────────────────────────────────────────────────────────────

def columns_for(spec: dict) -> list:
    """Display columns plus whatever this scan's filters and post-filters need."""
    cols = list(DISPLAY_COLS)
    if spec.get("perf"):
        cols.append(spec["perf"][0])
    post = spec.get("post") or {}
    for key, needed in POST_COLS.items():
        if post.get(key):
            cols += needed
    if post.get("ma"):
        cols.append(post["ma"][0])
    # de-dupe, preserve order
    return list(dict.fromkeys(cols))


def build_query(spec: dict, limit: int) -> Query:
    conds = [col("type") == "stock", col("exchange").isin(EXCHANGES)]

    lo, hi = spec.get("mcap", (None, None))
    if lo is not None and hi is not None:
        conds.append(col("market_cap_basic").between(lo, hi))
    elif lo is not None:
        conds.append(col("market_cap_basic") > lo)

    if spec.get("min_avg_vol"):
        conds.append(col("average_volume_60d_calc") > spec["min_avg_vol"])
    if spec.get("min_vol"):
        conds.append(col("volume") > spec["min_vol"])
    if spec.get("max_float"):
        conds.append(col("float_shares_outstanding") < spec["max_float"])
    if spec.get("min_volatility"):
        conds.append(col("Volatility.M") > spec["min_volatility"])
    if spec.get("perf"):
        field, threshold = spec["perf"]
        conds.append(col(field) > threshold)
    if spec.get("extra"):
        conds += spec["extra"]()

    # Rank by the metric the scan is actually about, so limit() truncates the
    # weakest matches rather than an arbitrary slice by today's move.
    sort_by = spec.get("sort_by") or (spec["perf"][0] if spec.get("perf") else "change")

    return (Query().set_markets("america")
            .select(*columns_for(spec))
            .where(*conds)
            .order_by(sort_by, ascending=False)
            .limit(limit))


def apply_post(df: pd.DataFrame, post: dict) -> pd.DataFrame:
    """Filters the screener language cannot express."""
    if not post or df.empty:
        return df
    keep = pd.Series(True, index=df.index)

    if "low_mult" in post and "price_52_week_low" in df.columns:
        keep &= df["close"] >= df["price_52_week_low"] * post["low_mult"]

    if "ma" in post:
        name, band = post["ma"]
        if name in df.columns:
            # MA at or below price, but not further than `band` below it —
            # price is extended above the MA yet still tight to it.
            keep &= (df[name] <= df["close"]) & (df[name] >= df["close"] * band)

    if post.get("sma10_gt_sma20") and {"SMA10", "SMA20"} <= set(df.columns):
        keep &= df["SMA10"] > df["SMA20"]

    return df[keep].copy()


def run_scan(spec: dict, limit: int):
    """Returns (spec, DataFrame, error_or_None)."""
    try:
        _, df = build_query(spec, limit).get_scanner_data()
        if df is None or df.empty:
            return spec, pd.DataFrame(), None
        df = apply_post(df, spec.get("post"))
        return spec, df, None
    except Exception as e:
        return spec, pd.DataFrame(), f"{type(e).__name__}: {e}"


# ── Output shaping ────────────────────────────────────────────────────────────

def num(v, dec=2):
    try:
        f = float(v)
        return None if f != f else round(f, dec)   # NaN check
    except (TypeError, ValueError):
        return None


PERF_KEYS = {"Perf.W": "perf_w", "Perf.1M": "perf_1m",
             "Perf.3M": "perf_3m", "Perf.6M": "perf_6m"}


def merge_rows(results: list) -> list:
    """One row per ticker, carrying the list of scans that matched it."""
    rows = {}
    for spec, df in results:
        for _, r in df.iterrows():
            ticker = r.get("name")
            if not isinstance(ticker, str) or not ticker:
                continue
            row = rows.setdefault(ticker, {
                "ticker": ticker, "name": None, "close": None, "chg": None,
                "vol": None, "mcap": None, "float": None, "volm": None,
                "rvol": None, "scans": [], "hits": 0,
            })
            if row["name"] is None and isinstance(r.get("description"), str):
                row["name"] = r["description"]
            for key, src, dec in (("close", "close", 2), ("chg", "change", 2),
                                  ("vol", "volume", 0), ("mcap", "market_cap_basic", 0),
                                  ("float", "float_shares_outstanding", 0),
                                  ("volm", "Volatility.M", 2),
                                  ("rvol", "relative_volume_10d_calc", 2)):
                if row[key] is None and src in df.columns:
                    row[key] = num(r.get(src), dec)
            for src, key in PERF_KEYS.items():
                if src in df.columns and row.get(key) is None:
                    row[key] = num(r.get(src), 2)
            if spec["id"] not in row["scans"]:
                row["scans"].append(spec["id"])
                row["hits"] += 1

    out = list(rows.values())
    # Highest conviction first: most scans hit, then biggest move.
    out.sort(key=lambda r: (-r["hits"], -(r["chg"] if r["chg"] is not None else -9e9)))
    return out


def write_history(out_dir: str, results: list, day: str):
    """Append-only per-month record of which tickers each scan caught."""
    hist_dir = os.path.join(out_dir, "scan_history")
    os.makedirs(hist_dir, exist_ok=True)
    path = os.path.join(hist_dir, day[:7] + ".json")

    history = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠  history unreadable, starting fresh: {e}", file=sys.stderr)

    history[day] = {
        spec["id"]: sorted(df["name"].dropna().unique().tolist())
        for spec, df in results if not df.empty and "name" in df.columns
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, sort_keys=True)
    print(f"📚 History updated: {path} ({day})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--limit", type=int, default=300, help="max rows per scan")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"🔍 Running {len(SCANS)} TradingView scans…")
    results, failures = [], []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(run_scan, s, args.limit) for s in SCANS]
        for fut in concurrent.futures.as_completed(futures):
            spec, df, err = fut.result()
            if err:
                failures.append((spec["id"], err))
                print(f"  ✗ {spec['id']}: {err}", file=sys.stderr)
            else:
                results.append((spec, df))
                print(f"  ✓ {spec['id']}: {len(df)} matches")

    if not results:
        # Every scan failed (TradingView blocked us / network down). Leave the
        # previous scans.json in place rather than publishing an empty board.
        print("✗ All scans failed — keeping the previous scans.json", file=sys.stderr)
        for sid, err in failures:
            print(f"    {sid}: {err}", file=sys.stderr)
        sys.exit(1)

    by_id = {spec["id"]: df for spec, df in results}
    scans_meta = [{
        "id": s["id"], "label": s["label"], "group": s["group"],
        "mcap_group": s.get("mcap_group"), "timeframe": s.get("timeframe"),
        "count": int(len(by_id.get(s["id"], []))),
        "ok": s["id"] in by_id,
    } for s in SCANS]

    rows = merge_rows(results)
    now = datetime.now(timezone.utc)
    payload = {
        "updated_utc": now.isoformat(),
        "updated_label": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z"),
        "source": "TradingView Screener",
        "scans": scans_meta,
        "rows": rows,
        "failed": [sid for sid, _ in failures],
    }

    path = os.path.join(args.out_dir, "scans.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    write_history(args.out_dir, results, now.strftime("%Y-%m-%d"))

    multi = sum(1 for r in rows if r["hits"] > 1)
    print(f"✅ {len(rows)} unique tickers ({multi} multi-hit) → {path}")
    if failures:
        print(f"⚠  {len(failures)} scan(s) failed: {', '.join(s for s, _ in failures)}")


if __name__ == "__main__":
    main()
