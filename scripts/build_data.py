"""
build_data.py  —  Mighty7under Market Dashboard
================================================
Fetches data from Yahoo Finance (yfinance) and writes:
  data/snapshot.json   — prices, % changes, sparklines
  data/events.json     — upcoming economic calendar events
  data/meta.json       — build timestamp + status

Usage:
  python scripts/build_data.py [--out-dir data]

GitHub Actions runs this daily at 16:35 ET (Mon-Fri).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf

# ── Symbol catalogue ──────────────────────────────────────────────────────────

GROUPS = {
    "futures": {
        "ES=F":       "S&P 500 E-mini",
        "NQ=F":       "Nasdaq 100 E-mini",
        "YM=F":       "Dow Jones E-mini",
        "RTY=F":      "Russell 2000 E-mini",
    },
    "volatility": {
        "^VIX":       "VIX Volatility Index",
        "DX-Y.NYB":   "US Dollar Index (DXY)",
    },
    "crypto": {
        "BTC-USD":    "Bitcoin",
        "ETH-USD":    "Ethereum",
        "SOL-USD":    "Solana",
    },
    "metals": {
        "GC=F":       "Gold",
        "SI=F":       "Silver",
        "HG=F":       "Copper",
        "PL=F":       "Platinum",
    },
    "energy": {
        "CL=F":       "WTI Crude Oil",
        "BZ=F":       "Brent Crude Oil",
        "NG=F":       "Natural Gas",
    },
    "yields": {
        "^IRX":       "3M T-Bill",
        "^FVX":       "5Y Treasury",
        "^TNX":       "10Y Treasury",
        "^TYX":       "30Y Treasury",
    },
    "global_indices": {
        "^N225":      "Nikkei 225",
        "^GDAXI":     "DAX",
        "^FTSE":      "FTSE 100",
        "^HSI":       "Hang Seng",
        "000001.SS":  "Shanghai Comp",
        "^AXJO":      "ASX 200",
        "^KS11":      "KOSPI",
        "^BSESN":     "BSE Sensex",
    },
    "etfs": {
        "SPY":        "SPDR S&P 500",
        "QQQ":        "Invesco QQQ",
        "IWM":        "iShares Russell 2000",
        "EFA":        "iShares MSCI EAFE",
        "VWO":        "Vanguard Emerg. Mkts",
        "GLD":        "SPDR Gold",
        "TLT":        "iShares 20Y+ Treasury",
        "HYG":        "iShares HY Corp Bond",
    },
    "sectors": {
        "XLK":        "Technology",
        "XLF":        "Financials",
        "XLV":        "Healthcare",
        "XLY":        "Consumer Discret.",
        "XLI":        "Industrials",
        "XLE":        "Energy",
        "XLU":        "Utilities",
        "XLRE":       "Real Estate",
        "XLB":        "Materials",
        "XLC":        "Comm Services",
        "XLP":        "Cons Staples",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def safe(v, dec=4):
    try:
        f = float(v)
        return None if (f != f) else round(f, dec)   # NaN check
    except Exception:
        return None

def pct(new, old, dec=2):
    try:
        return round((float(new) - float(old)) / abs(float(old)) * 100, dec)
    except Exception:
        return None

def fetch_group(symbols: dict) -> list:
    """Download 1y daily data for a symbol dict, return list of row dicts."""
    syms = list(symbols.keys())
    try:
        raw = yf.download(syms, period="1y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print(f"  ✗ Download failed: {e}", file=sys.stderr)
        return []

    # yfinance returns MultiIndex when multiple tickers, single-level for one
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": syms[0]})

    rows = []
    for sym, name in symbols.items():
        try:
            if sym not in close.columns:
                print(f"  ⚠  {sym} not in response", file=sys.stderr)
                continue
            s = close[sym].dropna()
            if len(s) < 6:
                continue

            price    = safe(s.iloc[-1])
            prev     = safe(s.iloc[-2])
            wk_ago   = safe(s.iloc[-6])
            hi52     = safe(s.max())
            ytd_s    = s[s.index >= f"{datetime.now().year}-01-01"]
            ytd_open = safe(ytd_s.iloc[0]) if len(ytd_s) else prev

            # 5-day sparkline (daily % changes)
            spark = []
            for i in range(-5, 0):
                try:
                    sp = pct(s.iloc[i], s.iloc[i - 1])
                    spark.append(sp if sp is not None else 0)
                except Exception:
                    spark.append(0)

            rows.append({
                "symbol":   sym,
                "name":     name,
                "price":    price,
                "d1":       pct(price, prev),
                "w1":       pct(price, wk_ago),
                "hi52pct":  pct(price, hi52),
                "ytd":      pct(price, ytd_open),
                "spark":    spark,
            })

        except Exception as e:
            print(f"  ⚠  {sym}: {e}", file=sys.stderr)

    return rows


def build_snapshot() -> dict:
    snapshot = {}
    for group, symbols in GROUPS.items():
        print(f"  Fetching {group} ({len(symbols)} symbols)…")
        snapshot[group] = fetch_group(symbols)
    return snapshot


def build_events() -> list:
    """
    Return a list of upcoming known economic events.
    investpy is unreliable / deprecated, so we return a static near-term
    calendar that you can extend manually or replace with a paid API later.
    """
    now = datetime.now(timezone.utc)
    events = [
        {"date": "2026-03-12", "time": "08:30 ET", "event": "CPI (Feb)",          "impact": "high",   "country": "US"},
        {"date": "2026-03-13", "time": "08:30 ET", "event": "PPI (Feb)",           "impact": "medium", "country": "US"},
        {"date": "2026-03-19", "time": "14:00 ET", "event": "FOMC Rate Decision",  "impact": "high",   "country": "US"},
        {"date": "2026-03-19", "time": "14:30 ET", "event": "Powell Press Conference","impact":"high",  "country": "US"},
        {"date": "2026-03-20", "time": "08:30 ET", "event": "Jobless Claims",      "impact": "medium", "country": "US"},
        {"date": "2026-03-28", "time": "08:30 ET", "event": "PCE Price Index (Feb)","impact":"high",   "country": "US"},
        {"date": "2026-04-02", "time": "08:30 ET", "event": "Nonfarm Payrolls (Mar)","impact":"high",  "country": "US"},
        {"date": "2026-04-10", "time": "08:30 ET", "event": "CPI (Mar)",           "impact": "high",   "country": "US"},
    ]
    # Filter to only future events
    future = [e for e in events if e["date"] >= now.strftime("%Y-%m-%d")]
    return future[:8]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="data", help="Output directory")
    args = parser.parse_args()

    out = args.out_dir
    os.makedirs(out, exist_ok=True)

    print("📡 Fetching market snapshot…")
    snapshot = build_snapshot()

    print("📅 Building economic calendar…")
    events = build_events()

    now_utc = datetime.now(timezone.utc).isoformat()

    # ── Write files ───────────────────────────────────────────────────────────
    with open(os.path.join(out, "snapshot.json"), "w") as f:
        json.dump(snapshot, f, indent=2)

    with open(os.path.join(out, "events.json"), "w") as f:
        json.dump(events, f, indent=2)

    meta = {
        "updated_utc": now_utc,
        "updated_cst": datetime.now(timezone(timedelta(hours=-6))).strftime("%Y-%m-%d %H:%M CST"),
        "source": "Yahoo Finance (yfinance)",
        "groups": {k: len(v) for k, v in snapshot.items()},
    }
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    total = sum(len(v) for v in snapshot.values())
    print(f"✅  {total} instruments saved to {out}/")
    print(f"🕐  {now_utc}")


if __name__ == "__main__":
    main()
