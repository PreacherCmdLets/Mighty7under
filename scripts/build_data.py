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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
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

# ── Pristine Market Analysis — symbol groups ─────────────────────────────────

PRISTINE_GROUPS = {
    "pristine_equity_alt": {
        "ZB=F":       "T-Bond Futures",
        "GC=F":       "Gold Futures",
        "ZN=F":       "10-Yr T-Note Futures",
        "BTC-USD":    "Bitcoin",
        "CL=F":       "Light Crude Oil Futures",
        "DX-Y.NYB":   "U.S. Dollar Index",
    },
    "pristine_global_eq": {
        "VTI":        "Total US Stock",
        "EEM":        "Emerging Markets",
        "IEV":        "Europe Equity",
        "VXUS":       "Total International Stock",
    },
    "pristine_us_indices": {
        "ARKK":       "Innovation",
        "TLT":        "Long-Term US Treasuries",
        "QQQ":        "Nasdaq",
        "SPY":        "S&P 500",
        "DIA":        "Dow Jones",
        "IWM":        "Russell 2000",
        "RSP":        "S&P 500 Equal Weight",
    },
    "pristine_sectors": {
        "GDX":        "Gold Miners",
        "XLY":        "Consumer Discretionary",
        "ITA":        "Aerospace + Defense",
        "IYR":        "Real Estate",
        "IGV":        "Software",
        "XHB":        "Home Builders",
        "KWEB":       "Chinese Technology",
        "XLK":        "Technology",
        "SOXX":       "Semiconductors",
        "XLU":        "Utilities",
        "XLI":        "Industrials",
        "TAN":        "Solar Energy",
        "IYT":        "Transportation",
        "KRE":        "Regional Banks",
        "XLP":        "Consumer Staples",
        "XLB":        "Materials",
        "XLF":        "Financials",
        "XRT":        "Retail",
        "XBI":        "Biotech",
        "XLV":        "Health Care",
        "XLE":        "Energy",
        "MSOS":       "US Cannabis",
        "BLOK":       "Blockchain",
    },
}

# ── RS Rank universes (RS Radar) ──────────────────────────────────────────────
# Sector Leaders uses the 11 Invesco EQUAL-WEIGHT sector ETFs (RSP*) so a few
# mega-caps can't mask what the average stock in a sector is doing.
# Industry RS Rank is a broad themed-ETF universe; ranks are percentiles
# WITHIN this list, so changing its membership changes everyone's rank.

RS_GROUPS = {
    "rs_sectors": {
        "RSPT": "Technology",
        "RSPF": "Financials",
        "RSPH": "Health Care",
        "RSPD": "Cons Disc",
        "RSPN": "Industrials",
        "RSPG": "Energy",
        "RSPU": "Utilities",
        "RSPR": "Real Estate",
        "RSPM": "Materials",
        "RSPC": "Comm Svcs",
        "RSPS": "Cons Stpl",
    },
    "rs_industries": {
        "SMH":  "Semiconductors",
        "IGV":  "Tech-Software",
        "CIBR": "Cybersecurity",
        "FINX": "FinTech",
        "IPAY": "Mobile Payments",
        "QTUM": "Quantum-Tech",
        "BLOK": "Blockchain",
        "WGMI": "Bitcoin Miners",
        "KARS": "Electric Cars",
        "LIT":  "Lithium",
        "ICLN": "Clean Energy",
        "TAN":  "Solar",
        "URA":  "Uranium",
        "GRID": "Power Grid",
        "XOP":  "Oil & Gas Exp.",
        "GDX":  "Gold Miners",
        "SIL":  "Silver Miners",
        "COPX": "Copper Miners",
        "REMX": "Rare Earth",
        "SLX":  "Steel",
        "WOOD": "Timber",
        "MOO":  "Agribusiness",
        "KRE":  "Regional Banks",
        "KIE":  "Insurance",
        "IAI":  "Broker-Dealers",
        "XRT":  "Retail",
        "PEJ":  "Leisure",
        "JETS": "Global Jets",
        "BOAT": "Marine Shipping",
        "IYT":  "Transportation",
        "ITB":  "Home Construction",
        "ITA":  "Aerospace + Defense",
        "UFO":  "Space",
        "IHI":  "Med. Devices",
        "XBI":  "Biotech",
        "IBB":  "Biotechnology",
        "KWEB": "China Internet",
        "IPO":  "IPO",
        "MSOS": "US Cannabis",
    },
}


# ── Pristine indicator calculations ──────────────────────────────────────────

def calc_sma(series, period):
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def calc_ema(series, period):
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def calc_atr(highs, lows, closes, period=20):
    """Average True Range (Wilder)."""
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def classify_stage(closes, fast_ma, slow_ma, lookback=30):
    """
    Pristine stage analysis. Iterates over last `lookback` bars to determine
    current stage based on price/MA relationships and transitions.
    Returns the current stage string (e.g. '2B', '4A', '1A').
    """
    n = len(closes)
    if n < lookback + 6 or fast_ma.isna().iloc[-1] or slow_ma.isna().iloc[-1]:
        return "N/A"

    start = max(0, n - lookback)
    prev_stage = None

    for i in range(start, n):
        close = closes.iloc[i]
        fast  = fast_ma.iloc[i]
        slow  = slow_ma.iloc[i]

        if pd.isna(fast) or pd.isna(slow):
            prev_stage = None
            continue

        price_above_fast = close > fast
        fast_above_slow  = fast > slow

        # Check if fast MA just crossed slow MA today
        if i > 0 and not pd.isna(fast_ma.iloc[i - 1]) and not pd.isna(slow_ma.iloc[i - 1]):
            prev_fast_above = fast_ma.iloc[i - 1] > slow_ma.iloc[i - 1]
            fast_crossed_above = fast_above_slow and not prev_fast_above
            fast_crossed_below = not fast_above_slow and prev_fast_above
        else:
            fast_crossed_above = False
            fast_crossed_below = False

        # Fast MA slope (compare to 5 bars ago)
        ref_idx = max(0, i - 5)
        ref_fast = fast_ma.iloc[ref_idx]
        if not pd.isna(ref_fast) and ref_fast != 0:
            fast_slope_pct = (fast - ref_fast) / abs(ref_fast) * 100
        else:
            fast_slope_pct = 0
        fast_rising = fast_slope_pct > 0
        steep = abs(fast_slope_pct) > 0.5

        ps = prev_stage or ""

        if fast_above_slow and price_above_fast:
            # UPTREND ZONE
            if ps.startswith("3") or ps.startswith("4"):
                stage = "2R"
            elif fast_crossed_above:
                stage = "2A"
            elif steep and fast_rising:
                stage = "2C"
            else:
                stage = "2B"

        elif fast_above_slow and not price_above_fast:
            # TOPPING ZONE
            if ps.startswith("2"):
                stage = "3A"
            elif ps.startswith("4"):
                stage = "3R"
            else:
                stage = "3B"

        elif not fast_above_slow and price_above_fast:
            # BOTTOMING ZONE
            if ps.startswith("4"):
                stage = "1A"
            elif ps.startswith("2"):
                stage = "1R"
            else:
                stage = "1B"

        elif not fast_above_slow and not price_above_fast:
            # DOWNTREND ZONE
            if ps.startswith("1"):
                stage = "4R"
            elif fast_crossed_below:
                stage = "4A"
            elif steep and not fast_rising:
                stage = "4C"
            else:
                stage = "4B"
        else:
            stage = "N/A"

        prev_stage = stage

    return prev_stage or "N/A"


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


def fetch_pristine_group(symbols: dict) -> list:
    """Download 1y daily OHLCV data and compute Pristine metrics."""
    syms = list(symbols.keys())
    try:
        raw = yf.download(syms, period="1y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print(f"  ✗ Download failed: {e}", file=sys.stderr)
        return []

    multi = isinstance(raw.columns, pd.MultiIndex)

    def col(field, sym):
        if multi:
            return raw[field][sym].dropna() if sym in raw[field].columns else pd.Series(dtype=float)
        else:
            return raw[field].dropna() if field in raw.columns else pd.Series(dtype=float)

    rows = []
    for sym, name in symbols.items():
        try:
            c = col("Close", sym)
            h = col("High", sym)
            lo = col("Low", sym)

            if len(c) < 210:
                # Need 200+ bars for 200-SMA; use what we have
                pass
            if len(c) < 6:
                continue

            # Align H/L/C on shared index
            idx = c.index.intersection(h.index).intersection(lo.index)
            c, h, lo = c.loc[idx], h.loc[idx], lo.loc[idx]

            price     = safe(c.iloc[-1])
            prev      = safe(c.iloc[-2])
            day_high  = safe(h.iloc[-1])
            day_low   = safe(lo.iloc[-1])

            # %D
            d1 = pct(price, prev)

            # ATR (20-day)
            atr_series = calc_atr(h, lo, c, period=20)
            atr_val    = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else None
            atr_pct    = (atr_val / price * 100) if (atr_val and price) else None

            # ATR D
            atr_d = round(d1 / atr_pct, 1) if (d1 is not None and atr_pct and atr_pct != 0) else None

            # DCR
            denom = day_high - day_low if (day_high and day_low) else 0
            dcr = round((price - day_low) / denom * 100, 1) if (denom and denom > 0) else None

            # 52WR
            hi52 = float(c.max())
            lo52 = float(c.min())
            w52r = round((price - lo52) / (hi52 - lo52) * 100, 1) if (hi52 - lo52) > 0 else None

            # MAx (50-day SMA default)
            sma50 = calc_sma(c, 50)
            sma50_val = sma50.iloc[-1] if not pd.isna(sma50.iloc[-1]) else None
            if sma50_val and atr_pct and atr_pct != 0:
                price_dist_pct = (price - sma50_val) / price * 100
                max_val = round(price_dist_pct / atr_pct, 1)
            else:
                max_val = None

            # Stage Analysis — ST (10 EMA vs 20 SMA)
            ema10  = calc_ema(c, 10)
            sma20  = calc_sma(c, 20)
            st = classify_stage(c, ema10, sma20, lookback=30)

            # Stage Analysis — LT (50 SMA vs 200 SMA)
            sma200 = calc_sma(c, 200)
            if pd.isna(sma200.iloc[-1]):
                lt = "N/A"
            else:
                lt = classify_stage(c, sma50, sma200, lookback=30)

            rows.append({
                "symbol":   sym,
                "name":     name,
                "price":    price,
                "d1":       d1,
                "atr_d":    atr_d,
                "dcr":      dcr,
                "w52r":     w52r,
                "max_val":  max_val,
                "st":       st,
                "lt":       lt,
            })

        except Exception as e:
            print(f"  ⚠  {sym}: {e}", file=sys.stderr)

    # Sort by %D descending
    rows.sort(key=lambda r: r.get("d1") or -9999, reverse=True)
    return rows


def fetch_rs_group(symbols: dict) -> list:
    """
    Download 1y daily closes and compute relative-strength percentile ranks.

    RS score per day = mean of trailing 1W / 1M / 3M returns. Each day the
    scores are percentile-ranked across the group (0 = weakest, 100 =
    strongest). Besides today's rank, the rank as of 1 day / 1 week /
    1 month ago is included so the dashboard can show rank movers.
    """
    syms = list(symbols.keys())
    try:
        raw = yf.download(syms, period="1y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print(f"  ✗ Download failed: {e}", file=sys.stderr)
        return []

    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": syms[0]})
    close = close.dropna(how="all")

    score = (
        close.pct_change(5,  fill_method=None)
        + close.pct_change(21, fill_method=None)
        + close.pct_change(63, fill_method=None)
    ) / 3

    n_valid = score.notna().sum(axis=1)
    rank = score.rank(axis=1, method="average")
    rank = rank.sub(1).div((n_valid - 1).clip(lower=1), axis=0) * 100

    def rank_at(sym, offset):
        try:
            v = rank[sym].iloc[offset]
            return None if pd.isna(v) else int(round(v))
        except Exception:
            return None

    rows = []
    for sym, name in symbols.items():
        try:
            if sym not in close.columns:
                print(f"  ⚠  {sym} not in response", file=sys.stderr)
                continue
            s = close[sym].dropna()
            if len(s) < 70:        # need 63 bars for the 3M leg
                continue

            price = safe(s.iloc[-1])

            # Stage analysis (same definitions as the M7 stage tables)
            st = classify_stage(s, calc_ema(s, 10), calc_sma(s, 20), lookback=30)
            sma200 = calc_sma(s, 200)
            if pd.isna(sma200.iloc[-1]):
                lt = "N/A"
            else:
                lt = classify_stage(s, calc_sma(s, 50), sma200, lookback=30)

            rows.append({
                "symbol":   sym,
                "name":     name,
                "price":    price,
                "rank":     rank_at(sym, -1),
                "rank_1d":  rank_at(sym, -2),
                "rank_1w":  rank_at(sym, -6),
                "rank_1m":  rank_at(sym, -22),
                "st":       st,
                "lt":       lt,
                "d1":       pct(price, s.iloc[-2]),
                "w1":       pct(price, s.iloc[-6]),
                "m1":       pct(price, s.iloc[-22]),
                "hi52pct":  pct(price, s.max()),
            })
        except Exception as e:
            print(f"  ⚠  {sym}: {e}", file=sys.stderr)

    rows.sort(key=lambda r: r["rank"] if r["rank"] is not None else -1, reverse=True)
    return rows


# ── Market Conditions (Dashboard) ─────────────────────────────────────────────

MC_INDEX_CARDS = {
    "RSP":     "S&P 500 Eq Wt",
    "QQQE":    "Nasdaq 100 Eq Wt",
    "IWM":     "Russell 2000",
    "DIA":     "Dow Jones",
    "BTC-USD": "Bitcoin",
    "^VIX":    "Volatility",
}


def build_market_conditions() -> dict:
    """
    Dashboard "Market Conditions" block:
      breadth       — % of the RS universe above key SMAs (+ MA alignment)
      breadth_hist  — [date, % above 50-SMA] pairs for ~1y (trend chart)
      performance   — SPY trailing returns and distance from 52-week high
      distribution  — IBD-style distribution-day counts for SPY / QQQ
      cards         — index cards with 21-EMA position and volume vs 50d avg
    """
    out = {}

    # Breadth across the whole RS universe (sectors + industries)
    syms = sorted(set(RS_GROUPS["rs_sectors"]) | set(RS_GROUPS["rs_industries"]))
    try:
        raw = yf.download(syms, period="2y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        close = close.dropna(how="all")
        last = close.iloc[-1]

        def pct_true(mask):
            return round(float(mask.mean() * 100), 1) if len(mask) else None

        breadth = {}
        sma_last = {}
        for p in (10, 20, 50, 200):
            sma = close.rolling(p, min_periods=p).mean().iloc[-1]
            sma_last[p] = sma
            valid = last.notna() & sma.notna()
            breadth[f"above_sma{p}"] = pct_true(last[valid] > sma[valid]) if valid.any() else None
        v = sma_last[20].notna() & sma_last[50].notna()
        breadth["sma20_gt_50"] = pct_true(sma_last[20][v] > sma_last[50][v]) if v.any() else None
        v = sma_last[50].notna() & sma_last[200].notna()
        breadth["sma50_gt_200"] = pct_true(sma_last[50][v] > sma_last[200][v]) if v.any() else None
        out["breadth"] = breadth

        # Daily % of universe above its 50-SMA, ~1 year (for the trend chart)
        sma50_all = close.rolling(50, min_periods=50).mean()
        valid_n = (close.notna() & sma50_all.notna()).sum(axis=1)
        above_n = ((close > sma50_all) & sma50_all.notna()).sum(axis=1)
        hist = (above_n / valid_n.clip(lower=1) * 100)[valid_n > 0].iloc[-252:]
        out["breadth_hist"] = [[d.strftime("%Y-%m-%d"), round(float(x), 1)]
                               for d, x in hist.items()]
    except Exception as e:
        print(f"  ⚠ market conditions breadth: {e}", file=sys.stderr)

    # SPY / QQQ distribution days + SPY performance
    try:
        raw = yf.download(["SPY", "QQQ"], period="1y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        dist = {}
        for sym in ("SPY", "QQQ"):
            c = raw["Close"][sym].dropna()
            vol = raw["Volume"][sym].reindex(c.index)
            # Distribution day: close down >= 0.2% on higher volume than prior day
            dd = (c.pct_change() <= -0.002) & (vol > vol.shift(1))
            dist[sym] = {"today": int(dd.iloc[-25:].sum()),
                         "prev":  int(dd.iloc[-26:-1].sum())}
        out["distribution"] = dist

        spy = raw["Close"]["SPY"].dropna()
        ytd = spy[spy.index >= f"{datetime.now().year}-01-01"]
        out["performance"] = {
            "ytd":          pct(spy.iloc[-1], ytd.iloc[0]) if len(ytd) else None,
            "w1":           pct(spy.iloc[-1], spy.iloc[-6]),
            "m1":           pct(spy.iloc[-1], spy.iloc[-22]),
            "y1":           pct(spy.iloc[-1], spy.iloc[0]),
            "off_52w_high": pct(spy.iloc[-1], spy.max()),
        }
    except Exception as e:
        print(f"  ⚠ market conditions distribution: {e}", file=sys.stderr)

    # Treasury yield history (10Y / 5Y / 3M) for the yields chart
    try:
        raw = yf.download(["^TNX", "^FVX", "^IRX"], period="1y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        c = raw["Close"].dropna(how="all").ffill()
        out["yields_hist"] = [
            [d.strftime("%Y-%m-%d"),
             safe(r.get("^TNX"), 3), safe(r.get("^FVX"), 3), safe(r.get("^IRX"), 3)]
            for d, r in c.iterrows()
        ]
    except Exception as e:
        print(f"  ⚠ market conditions yields: {e}", file=sys.stderr)

    # Index cards: 21-EMA position + volume vs 50-day average
    try:
        raw = yf.download(list(MC_INDEX_CARDS.keys()), period="1y", interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        cards = []
        for sym, name in MC_INDEX_CARDS.items():
            try:
                if sym not in raw["Close"].columns:
                    print(f"  ⚠  {sym} not in response", file=sys.stderr)
                    continue
                c = raw["Close"][sym].dropna()
                if len(c) < 60:
                    continue
                price = safe(c.iloc[-1])
                ema21 = calc_ema(c, 21).iloc[-1]
                vol = raw["Volume"][sym].reindex(c.index).dropna()
                vol_chg = None
                if len(vol) > 51:
                    avg50 = vol.iloc[-51:-1].mean()
                    if avg50 and avg50 > 0:
                        vol_chg = round(float(vol.iloc[-1] / avg50 - 1) * 100, 1)
                cards.append({
                    "symbol":  sym,
                    "name":    name,
                    "price":   price,
                    "d1":      pct(price, c.iloc[-2]),
                    "ema21":   "above" if (price is not None and not pd.isna(ema21)
                                           and price > ema21) else "below",
                    "vol_chg": vol_chg,
                })
            except Exception as e:
                print(f"  ⚠  card {sym}: {e}", file=sys.stderr)
        out["cards"] = cards
    except Exception as e:
        print(f"  ⚠ market conditions cards: {e}", file=sys.stderr)

    return out


def build_snapshot() -> dict:
    snapshot = {}
    for group, symbols in GROUPS.items():
        print(f"  Fetching {group} ({len(symbols)} symbols)…")
        snapshot[group] = fetch_group(symbols)

    # Mighty7under Market Analysis groups (with full indicator calculations)
    for group, symbols in PRISTINE_GROUPS.items():
        print(f"  Fetching {group} ({len(symbols)} symbols)…")
        snapshot[group] = fetch_pristine_group(symbols)

    # RS rank groups (RS Radar movers / Sector Leaders / Industry RS)
    for group, symbols in RS_GROUPS.items():
        print(f"  Fetching {group} ({len(symbols)} symbols)…")
        snapshot[group] = fetch_rs_group(symbols)

    print("  Building market conditions…")
    snapshot["market_conditions"] = build_market_conditions()

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
        "updated_cst": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z"),
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
