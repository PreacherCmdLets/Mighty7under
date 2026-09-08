"""
backtest.py  —  Mighty7under Scan Validation
============================================
Answers the only question that matters about a screener: do its picks go up?

Two independent passes, both written to data/backtest.json:

1. RECONSTRUCTED — replays the price-based scan rules across a liquid US
   universe over ~2 years of daily bars, giving thousands of historical
   detections immediately. Honest caveats, stated in the output and the UI:
     * survivorship bias — the universe is what trades today, so names that
       were delisted or collapsed are missing, which flatters results;
     * market-cap / float / volatility gates use today's values, not the
       values as of the historical date;
     * scans with fundamental filters (EPS/revenue growth) cannot be
       reconstructed at all — no point-in-time fundamentals — so they are
       excluded here and appear only in the live pass.

2. LIVE — forward returns on the actual picks recorded in
   data/scan_history/*.json. Unbiased and covers every scan, but only as
   deep as the history recorded so far.

Every result is shown against a baseline: the average forward return of the
whole universe over the same window. A momentum scan in a rising market looks
great until you compare it with buying anything at random.

Usage:
  python scripts/backtest.py [--out-dir data] [--universe 800] [--years 2]
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

SCAN_URL = "https://scanner.tradingview.com/america/scan?label-product=screener-stock"
HORIZONS = [1, 5, 10, 20]          # trading days forward
SMALL_CAP_MAX = 10e9

# Price-based scan rules, mirroring scripts/run_scans.py. `perf` is the
# trailing-return column and threshold; `band` keeps price tight to its 10-day
# SMA; `low_mult` demands price be well off the 52-week low.
PRICE_SCANS = {
    "mom_1w_small": {"label": "Momentum 1W · Small", "perf": ("p5", 20),
                     "band": 0.80, "low_mult": 1.50, "cap": "small", "volm": 3},
    "mom_1m_small": {"label": "Momentum 1M · Small", "perf": ("p21", 30),
                     "band": 0.80, "low_mult": 1.50, "cap": "small", "volm": 3},
    "mom_3m_small": {"label": "Momentum 3M · Small", "perf": ("p63", 70),
                     "band": 0.80, "low_mult": 1.50, "cap": "small", "volm": 3},
    "mom_6m_small": {"label": "Momentum 6M · Small", "perf": ("p126", 100),
                     "band": 0.80, "low_mult": 1.50, "cap": "small", "volm": 3},
    "mom_1w_large": {"label": "Momentum 1W · Large", "perf": ("p5", 20),
                     "band": 0.90, "low_mult": 1.50, "cap": "large"},
    "mom_1m_large": {"label": "Momentum 1M · Large", "perf": ("p21", 30),
                     "band": 0.90, "low_mult": 1.50, "cap": "large"},
    "mom_3m_large": {"label": "Momentum 3M · Large", "perf": ("p63", 70),
                     "band": 0.90, "low_mult": 1.50, "cap": "large"},
    "mom_6m_large": {"label": "Momentum 6M · Large", "perf": ("p126", 100),
                     "band": 0.90, "low_mult": 1.50, "cap": "large"},
    "daily_tightness": {"label": "Daily Tightness Swing", "tightness": True,
                        "low_mult": 1.50, "cap": "any", "volm": 3.5},
}

SCAN_LABELS = {k: v["label"] for k, v in PRICE_SCANS.items()}
SCAN_LABELS.update({
    "fundamental_growth": "Fundamental Growth",
    "post_earnings_base": "Post-Earnings Cont. Base",
    "strongest_jk": "Strongest Stock (JK)",
    "strongest_10b_jk": "Strongest Stock 10B Rev 30 (JK)",
    "premarket_gappers": "Pre-Market Gappers",
})


def load_universe(limit: int) -> pd.DataFrame:
    """Most liquid US common stocks, with today's cap/float for the gates."""
    payload = {
        "markets": ["america"],
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "exchange", "operation": "in_range",
             "right": ["NASDAQ", "NYSE", "AMEX"]},
            {"left": "market_cap_basic", "operation": "greater", "right": 300e6},
            {"left": "average_volume_60d_calc", "operation": "greater", "right": 300_000},
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": ["stock"]}, "tickers": []},
        "columns": ["name", "market_cap_basic", "float_shares_outstanding"],
        "sort": {"sortBy": "average_volume_60d_calc", "sortOrder": "desc"},
        "range": [0, limit],
    }
    r = requests.post(SCAN_URL, json=payload, timeout=60)
    r.raise_for_status()
    rows = []
    for item in r.json().get("data") or []:
        d = item.get("d") or []
        if len(d) < 3 or not isinstance(d[0], str):
            continue
        rows.append({"ticker": d[0], "mcap": d[1], "float": d[2]})
    return pd.DataFrame(rows)


def download_prices(tickers: list, years: int, chunk: int = 200):
    """Daily OHLC for the universe, batched so one bad chunk cannot sink the run."""
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
        else:                                     # single surviving ticker
            closes.append(raw[["Close"]].rename(columns={"Close": batch[0]}))
            highs.append(raw[["High"]].rename(columns={"High": batch[0]}))
            lows.append(raw[["Low"]].rename(columns={"Low": batch[0]}))
        print(f"  ✓ prices {i + len(batch)}/{len(tickers)}")
    if not closes:
        return None, None, None
    return (pd.concat(closes, axis=1).dropna(how="all"),
            pd.concat(highs, axis=1), pd.concat(lows, axis=1))


def build_frames(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame) -> dict:
    """Per-date, per-ticker metrics the scan rules are expressed in."""
    f = {"close": close}
    for name, n in (("p5", 5), ("p21", 21), ("p63", 63), ("p126", 126)):
        f[name] = close.pct_change(n, fill_method=None) * 100
    f["sma10"] = close.rolling(10, min_periods=10).mean()
    f["sma20"] = close.rolling(20, min_periods=20).mean()
    f["ema5"] = close.ewm(span=5, adjust=False, min_periods=5).mean()
    f["low52"] = close.rolling(252, min_periods=60).min()
    # Proxy for TradingView's Volatility.M: mean daily range as % of close.
    f["volm"] = ((high - low) / close).rolling(21, min_periods=21).mean() * 100
    return f


def detection_mask(f: dict, spec: dict, cap_mask: pd.Series) -> pd.DataFrame:
    close = f["close"]
    m = pd.DataFrame(True, index=close.index, columns=close.columns)

    if spec.get("tightness"):
        # price above a rising-ish structure and tight to the 5-day EMA
        m &= (f["ema5"] <= close) & (f["ema5"] >= close * 0.97) & (f["sma10"] > f["sma20"])
    else:
        key, thresh = spec["perf"]
        m &= f[key] > thresh
        m &= (f["sma10"] <= close) & (f["sma10"] >= close * spec["band"])

    m &= close >= f["low52"] * spec["low_mult"]
    if spec.get("volm"):
        m &= f["volm"] > spec["volm"]

    # Static cap/float gate, broadcast down every date (see module docstring).
    m &= pd.DataFrame(np.tile(cap_mask.values, (len(m), 1)),
                      index=m.index, columns=m.columns)
    return m.fillna(False)


def summarise(name: str, label: str, mask: pd.DataFrame, fwd: dict,
              baseline: dict) -> dict | None:
    """Aggregate the forward returns of every detection this mask produced."""
    out = {"id": name, "label": label}
    n = 0
    for h in HORIZONS:
        vals = fwd[h].where(mask).stack(dropna=True)
        vals = vals[np.isfinite(vals)]
        if h == HORIZONS[-1]:
            n = int(vals.size)
            out["win"] = round(float((vals > 0).mean()) * 100, 1) if vals.size else None
            out["median"] = round(float(vals.median()) * 100, 2) if vals.size else None
            out["best"] = round(float(vals.max()) * 100, 2) if vals.size else None
            out["worst"] = round(float(vals.min()) * 100, 2) if vals.size else None
        out[f"r{h}"] = round(float(vals.mean()) * 100, 2) if vals.size else None
        b = baseline.get(f"r{h}")
        out[f"edge{h}"] = (round(out[f"r{h}"] - b, 2)
                           if out[f"r{h}"] is not None and b is not None else None)
    out["n"] = n
    return out if n else None


def live_validation(out_dir: str, close: pd.DataFrame, fwd: dict,
                    baseline: dict) -> list:
    """Forward returns on the picks actually recorded by run_scans.py."""
    picks = {}          # scan_id -> list of (date, ticker)
    for path in sorted(glob.glob(os.path.join(out_dir, "scan_history", "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                month = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠  {path}: {e}", file=sys.stderr)
            continue
        for day, scans in month.items():
            for sid, tickers in (scans or {}).items():
                for t in tickers or []:
                    picks.setdefault(sid, []).append((day, t))

    rows = []
    for sid, entries in sorted(picks.items()):
        rec = {"id": sid, "label": SCAN_LABELS.get(sid, sid), "n": 0}
        vals_by_h = {h: [] for h in HORIZONS}
        for day, ticker in entries:
            if ticker not in close.columns:
                continue
            idx = close.index.searchsorted(pd.Timestamp(day))
            if idx >= len(close.index):
                continue
            for h in HORIZONS:
                try:
                    v = fwd[h][ticker].iloc[idx]
                except (KeyError, IndexError):
                    continue
                if pd.notna(v) and np.isfinite(v):
                    vals_by_h[h].append(float(v))
        for h in HORIZONS:
            v = vals_by_h[h]
            rec[f"r{h}"] = round(float(np.mean(v)) * 100, 2) if v else None
            b = baseline.get(f"r{h}")
            rec[f"edge{h}"] = (round(rec[f"r{h}"] - b, 2)
                               if rec[f"r{h}"] is not None and b is not None else None)
        last = vals_by_h[HORIZONS[-1]]
        rec["n"] = len(last)
        rec["win"] = round(float(np.mean([x > 0 for x in last])) * 100, 1) if last else None
        rec["median"] = round(float(np.median(last)) * 100, 2) if last else None
        if any(rec[f"r{h}"] is not None for h in HORIZONS):
            rows.append(rec)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--universe", type=int, default=800)
    ap.add_argument("--years", type=int, default=2)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"🧪 Loading universe (top {args.universe} by volume)…")
    uni = load_universe(args.universe)
    if uni.empty:
        print("✗ Empty universe — keeping previous backtest.json", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ {len(uni)} tickers")

    print(f"📉 Downloading {args.years}y of daily bars…")
    close, high, low = download_prices(uni["ticker"].tolist(), args.years)
    if close is None or close.empty:
        print("✗ No price data — keeping previous backtest.json", file=sys.stderr)
        sys.exit(1)

    cols = [c for c in close.columns if c in set(uni["ticker"])]
    close, high, low = close[cols], high.reindex(columns=cols), low.reindex(columns=cols)
    uni = uni[uni["ticker"].isin(cols)].set_index("ticker").reindex(cols)
    print(f"  ✓ {close.shape[1]} tickers × {close.shape[0]} sessions")

    frames = build_frames(close, high, low)
    fwd = {h: close.shift(-h) / close - 1 for h in HORIZONS}

    # Baseline: hold anything in the universe for the same horizon.
    baseline = {}
    for h in HORIZONS:
        v = fwd[h].stack(dropna=True)
        v = v[np.isfinite(v)]
        baseline[f"r{h}"] = round(float(v.mean()) * 100, 2) if v.size else None
    print(f"  ✓ baseline 20d: {baseline.get('r20')}%")

    mcap = uni["mcap"].astype(float)
    cap_masks = {
        "small": (mcap > 300e6) & (mcap <= SMALL_CAP_MAX),
        "large": mcap > SMALL_CAP_MAX,
        "any":   mcap > 300e6,
    }

    print("🔁 Replaying price-based scans…")
    recon = []
    for name, spec in PRICE_SCANS.items():
        try:
            mask = detection_mask(frames, spec, cap_masks[spec["cap"]])
            row = summarise(name, spec["label"], mask, fwd, baseline)
            if row:
                recon.append(row)
                print(f"  ✓ {name}: {row['n']} detections, "
                      f"20d {row['r20']}% vs baseline {baseline['r20']}%")
            else:
                print(f"  · {name}: no detections")
        except Exception as e:
            print(f"  ✗ {name}: {type(e).__name__}: {e}", file=sys.stderr)

    print("📌 Validating recorded picks…")
    live = live_validation(args.out_dir, close, fwd, baseline)
    print(f"  ✓ {len(live)} scan(s) with recorded picks")

    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "updated_label": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z"),
        "horizons": HORIZONS,
        "universe_size": int(close.shape[1]),
        "sessions": int(close.shape[0]),
        "window": {"start": str(close.index[0].date()), "end": str(close.index[-1].date())},
        "baseline": baseline,
        "reconstructed": sorted(recon, key=lambda r: -(r.get("edge20") or -99)),
        "live": sorted(live, key=lambda r: -(r.get("n") or 0)),
    }
    path = os.path.join(args.out_dir, "backtest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"✅ {len(recon)} reconstructed + {len(live)} live → {path}")


if __name__ == "__main__":
    main()
