"""
finviz_screens.py  —  Mighty7under Finviz Saved Screens
=======================================================
Runs the user's saved Finviz screens and writes data/finviz_screens.json in
the same deduped shape as scans.json: one row per ticker carrying every screen
that caught it, so multi-hit names surface on their own.

The `preset=` ids in a finviz URL are account-scoped and are NOT needed — the
`f=` filter string is the whole screen, and it works anonymously. Each entry
below is that filter string copied verbatim from the saved URL, so the screen
here is exactly the screen in the browser.

finviz sits behind Cloudflare and blocks by IP reputation. finvizfinance
raises FinvizBlockedError for that; a total failure leaves the previous file
alone rather than publishing an empty board.

Usage:
  python scripts/finviz_screens.py [--out-dir data] [--limit 100]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from finvizfinance.screener.overview import Overview

# id, label, group, and the verbatim `f=` string from the saved screen URL.
SCREENS = [
    # ── Themes & squeeze ─────────────────────────────────────────────────────
    {"id": "hot_theme", "label": "Hot Theme", "group": "Themes & Squeeze",
     "f": "sh_avgvol_o2000,sh_curvol_o1000,sh_float_u100,sh_insttrans_pos,"
          "sh_short_high,ta_perf_13w30o,ta_volatility_wo5"},
    {"id": "high_tight_flag", "label": "High Tight Flag", "group": "Themes & Squeeze",
     "f": "cap_smallover,ind_stocksonly,sh_avgvol_o1000,sh_float_u100,sh_short_o30"},
    {"id": "pm_hottest", "label": "PM Hottest", "group": "Themes & Squeeze",
     "f": "cap_smallover,sh_avgvol_o1000,sh_float_u100,sh_short_o30"},

    # ── Institutional & growth ───────────────────────────────────────────────
    {"id": "net_pos_inst", "label": "Net-Positive Institution",
     "group": "Institutional & Growth",
     "f": "cap_midover,fa_salesqoq_high,fa_salesyoyttm_high,sh_avgvol_o500,"
          "sh_curvol_o2000,sh_insttrans_pos,ta_highlow20d_b5h,ta_highlow50d_b5h,"
          "ta_volatility_wo4"},
    # "Post-Market CANSLIM" had a byte-identical filter string to this screen,
    # so it was dropped rather than shipping two chips that always agree.
    {"id": "adr_weekly", "label": "ADR% Weekly", "group": "Institutional & Growth",
     "f": "cap_midover,fa_salesqoq_high,fa_salesyoyttm_high,sh_avgvol_o2000,"
          "sh_curvol_o1000,sh_insttrans_pos,ta_highlow20d_a5h,ta_highlow50d_a5h,"
          "ta_volatility_wo4"},
    {"id": "fundies", "label": "Fundies", "group": "Institutional & Growth",
     "f": "fa_epsqoq_o20,fa_roe_o20,fa_salesqoq_o20,geo_usa,sh_avgvol_o500,"
          "sh_price_o20,ta_highlow52w_b0to10h"},
    {"id": "ipo", "label": "IPO", "group": "Institutional & Growth",
     "f": "cap_midover,ipodate_prevyear,sh_avgvol_o1000,sh_insttrans_pos"},

    # ── Momentum & setups ────────────────────────────────────────────────────
    {"id": "bull_snorts", "label": "Bull Snorts", "group": "Momentum & Setups",
     "f": "sh_avgvol_o500,sh_price_o20,sh_relvol_o3"},
    {"id": "gappers", "label": "Gappers", "group": "Momentum & Setups",
     "f": "sh_avgvol_o500,sh_price_o20,ta_gap_u3"},
    {"id": "doublers", "label": "Doublers", "group": "Momentum & Setups",
     "f": "sh_avgvol_o500,sh_price_o20,ta_perf2_52w100o"},
    {"id": "new_high", "label": "New High", "group": "Momentum & Setups",
     "f": "sh_avgvol_o500,sh_price_o20,ta_highlow52w_nh"},
    {"id": "pm_base", "label": "PM Base", "group": "Momentum & Setups",
     "f": "cap_smallover,sh_avgvol_o1000,sh_curvol_o1000,sh_insttrans_pos,"
          "sh_price_o1,ta_alltime_b70h,ta_highlow50d_a15h,ta_highlow52w_b30h,"
          "ta_perf_ytddown,ta_sma200_-20to20-a,ta_volatility_wo4"},

    # ── ETFs ─────────────────────────────────────────────────────────────────
    {"id": "liquid_etfs", "label": "Liquid ETFs", "group": "ETFs",
     "f": "ind_exchangetradedfund,sh_avgvol_o1000,ta_volatility_wo3"},
]

MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def num(v):
    """Finviz renders numbers as '1.5B', '2.50%', '1,234,567' or '-'."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "nan", "None"):
        return None
    mult = 1.0
    if s and s[-1] in MULT:
        mult, s = MULT[s[-1]], s[:-1]
    try:
        return round(float(s) * mult, 4)
    except ValueError:
        return None


def run_screen(spec: dict, limit: int):
    """Fetch one saved screen. Returns (DataFrame, error_or_None)."""
    try:
        screener = Overview()
        # set_filter() only speaks finvizfinance's human-readable filter dict.
        # These screens are saved as raw finviz filter codes, so drive the
        # request params directly — that keeps them byte-identical to the URLs.
        screener.request_params = {"v": "111", "f": spec["f"], "ft": "4"}
        df = screener.screener_view(limit=limit, verbose=0, sleep_sec=1)
        return (df if df is not None else pd.DataFrame()), None
    except Exception as e:
        return pd.DataFrame(), f"{type(e).__name__}: {e}"


def merge_rows(results: list) -> list:
    """One row per ticker, carrying every screen that matched it."""
    rows = {}
    for spec, df in results:
        if df is None or df.empty or "Ticker" not in df.columns:
            continue
        for _, r in df.iterrows():
            t = r.get("Ticker")
            if not isinstance(t, str) or not t:
                continue
            row = rows.setdefault(t, {
                "ticker": t, "name": None, "sector": None, "industry": None,
                "close": None, "chg": None, "vol": None, "mcap": None,
                "pe": None, "screens": [], "hits": 0,
            })
            if row["name"] is None:
                row["name"] = r.get("Company")
                row["sector"] = r.get("Sector")
                row["industry"] = r.get("Industry")
            for key, col in (("close", "Price"), ("chg", "Change"),
                             ("vol", "Volume"), ("mcap", "Market Cap"), ("pe", "P/E")):
                if row[key] is None and col in df.columns:
                    row[key] = num(r.get(col))
            if spec["id"] not in row["screens"]:
                row["screens"].append(spec["id"])
                row["hits"] += 1

    out = list(rows.values())
    out.sort(key=lambda r: (-r["hits"], -(r["chg"] if r["chg"] is not None else -9e9)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--limit", type=int, default=100, help="max rows per screen")
    ap.add_argument("--delay", type=float, default=1.5,
                    help="seconds between screens — finviz rate-limits")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"🔎 Running {len(SCREENS)} Finviz screens…")
    results, failures = [], []
    for i, spec in enumerate(SCREENS):
        df, err = run_screen(spec, args.limit)
        if err:
            failures.append((spec["id"], err))
            print(f"  ✗ {spec['id']}: {err}", file=sys.stderr)
        else:
            results.append((spec, df))
            print(f"  ✓ {spec['id']}: {len(df)} matches")
        if i < len(SCREENS) - 1:
            time.sleep(args.delay)          # be a polite scraper

    if not results:
        print("✗ All screens failed — keeping the previous finviz_screens.json",
              file=sys.stderr)
        sys.exit(1)

    by_id = {spec["id"]: df for spec, df in results}
    meta = [{
        "id": s["id"], "label": s["label"], "group": s["group"],
        "count": int(len(by_id.get(s["id"], []))),
        "ok": s["id"] in by_id,
        "url": f"https://finviz.com/screener.ashx?v=111&f={s['f']}&ft=4",
    } for s in SCREENS]

    rows = merge_rows(results)
    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "updated_label": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z"),
        "source": "Finviz",
        "screens": meta,
        "rows": rows,
        "failed": [sid for sid, _ in failures],
    }
    path = os.path.join(args.out_dir, "finviz_screens.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    multi = sum(1 for r in rows if r["hits"] > 1)
    print(f"✅ {len(rows)} unique tickers ({multi} multi-hit) → {path}")
    if failures:
        print(f"⚠  {len(failures)} screen(s) failed: {', '.join(s for s, _ in failures)}")


if __name__ == "__main__":
    main()
