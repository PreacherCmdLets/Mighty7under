"""
generate_fundamental.py  —  Mighty7under AI Fundamental Research
=================================================================
For each ticker in the "Primary Focus" watchlist:
  1. Scrapes 6 sources via Playwright (financials, forecast, stats,
     overview, insider transactions, options chain)
  2. Sends scraped data + analysis prompt to Claude via Anthropic API
  3. Saves report to data/research/<TICKER>_fundamental.json

Usage:
  python scripts/generate_fundamental.py            # all Primary Focus tickers
  python scripts/generate_fundamental.py --ticker AAPL   # single ticker
  python scripts/generate_fundamental.py --force    # ignore cache

Runs automatically every Sunday via GitHub Actions.
Requires: ANTHROPIC_API_KEY environment variable
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────

WATCHLIST_FILE   = Path("data/watchlists.json")
OUTPUT_DIR       = Path("data/research")
CACHE_MAX_DAYS   = 6          # skip if report is fresher than this
SCRAPE_SLICE     = 3000       # chars per page scrape
OPTIONS_SLICE    = 2500
CLAUDE_MODEL     = "claude-sonnet-4-6"
INTER_TICKER_DELAY = 3        # seconds between tickers (rate limit safety)

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_primary_focus():
    data = json.loads(WATCHLIST_FILE.read_text())
    wl = next((w for w in data if w["name"] == "Primary Focus"), None)
    if not wl:
        print("ERROR: 'Primary Focus' watchlist not found in watchlists.json")
        sys.exit(1)
    return [s.split(":")[1] for s in wl["symbols"]]


def output_path(ticker):
    return OUTPUT_DIR / f"{ticker}_fundamental.json"


def is_fresh(ticker):
    p = output_path(ticker)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text())
        generated = datetime.fromisoformat(data["generated_at"])
        age = datetime.now(timezone.utc) - generated
        return age < timedelta(days=CACHE_MAX_DAYS)
    except Exception:
        return False


def scrape_ticker(ticker):
    """Scrape all 6 sources for a ticker. Returns dict of raw text per source."""
    upper = ticker.upper()
    lower = ticker.lower()
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

        def fetch(url, key, chars=SCRAPE_SLICE):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                text = page.evaluate(f"document.body.innerText.slice(0,{chars})")
                results[key] = text
                print(f"  OK {key}")
            except Exception as e:
                results[key] = f"[scrape failed: {e}]"
                print(f"  FAIL {key}: {e}")

        fetch(f"https://stockanalysis.com/stocks/{lower}/financials/",         "financials")
        fetch(f"https://stockanalysis.com/stocks/{lower}/forecast/",           "forecast")
        fetch(f"https://stockanalysis.com/stocks/{lower}/statistics/",         "statistics")
        fetch(f"https://stockanalysis.com/stocks/{lower}/",                    "overview")
        fetch(f"https://finance.yahoo.com/quote/{upper}/insider-transactions/","insiders")
        fetch(f"https://finance.yahoo.com/quote/{upper}/options/",             "options", OPTIONS_SLICE)

        browser.close()

    return results


def build_prompt(ticker, scraped):
    return f"""You are a professional equity research analyst. Using ONLY the live data below, generate a full institutional-grade fundamental research report for {ticker}.

## SCRAPED DATA

### Financials (StockAnalysis)
{scraped.get('financials', 'N/A')}

### Analyst Forecast (StockAnalysis)
{scraped.get('forecast', 'N/A')}

### Statistics & Valuation (StockAnalysis)
{scraped.get('statistics', 'N/A')}

### Overview & News (StockAnalysis)
{scraped.get('overview', 'N/A')}

### Insider Transactions (Yahoo Finance)
{scraped.get('insiders', 'N/A')}

### Options Chain (Yahoo Finance)
{scraped.get('options', 'N/A')}

## REPORT FORMAT

Produce this exact report. Every field must contain real numbers from the data above — no placeholders:

---

# **{ticker} — FUNDAMENTAL RESEARCH**

## EXECUTIVE SUMMARY
**[BUY/SELL/HOLD]** with $X price target (X% upside/downside) over 12 months. [2-sentence thesis with key catalyst]. [Risk/reward framing].

## FUNDAMENTAL ANALYSIS
**Recent Metrics:** Revenue $X (+Y% YoY) | Non-GAAP EPS $X | Gross Margin X% | Op Margin X% | FCF $X
**Peer Comparison:** [Table: Company | Fwd P/E | EV/EBITDA | P/S — 3–4 named peers]
**Forward Outlook:** [Next qtr guidance, full-year consensus, growth trajectory with specific numbers]

## CATALYST ANALYSIS
**Near-term (0–6 months):** [Dated events — next earnings date, product launches, regulatory decisions]
**Medium-term (6–24 months):** [Strategic initiatives, market expansion, pipeline milestones]
**Event-driven:** [M&A potential, index changes, special dividends, spinoffs]

## VALUATION & PRICE TARGETS
Consensus $X (range $low–$high, N analysts). Bull $X / Base $X / Bear $X with one scenario each. Probability weighting X%/X%/X%. Probability-weighted target $X vs current $X = X% upside/downside.

## RISK ASSESSMENT
**Company risks:** [3 specific risks with context]
**Macro risks:** [Rate sensitivity, sector rotation, regulatory/geopolitical]
**Position sizing:** X%–Y% based on beta X.X. ESG note if material.

## MARKET POSITIONING
[YTD vs relevant sector ETF. Relative strength vs S&P 500. Rotation context.]

## INSIDER SIGNALS
| Name | Role | Action | Shares | Price | Total | Date |
|------|------|--------|--------|-------|-------|------|
| ... | ... | ... | ... | ... | ... | ... |

[6-month buy/sell summary. Top 2 institutional holders with %. Buyback status. Behavioral pattern note.]

## OPTIONS INTELLIGENCE
[Near-ATM IV%. Flow bias: bullish/bearish/neutral based on chain data. Key OI levels.]

---

## RECOMMENDATION SUMMARY
| Metric | Value |
|--------|-------|
| **Rating** | BUY / SELL / HOLD |
| **Conviction** | High / Medium / Low |
| **Price Target** | $X |
| **Timeframe** | 12 months |
| **Upside/Downside** | +X% / -X% |
| **Position Size** | X%–Y% |

---

*This analysis is for educational purposes only. Not financial advice. All investments carry risk of loss.*
*Generated by Mighty7under AI Research — Fundamental*
"""


def generate_report(ticker, scraped):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = build_prompt(ticker, scraped)

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def save_report(ticker, report_text, scraped_at):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = output_path(ticker)
    payload = {
        "ticker": ticker,
        "type": "fundamental",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scraped_at": scraped_at,
        "report": report_text
    }
    path.write_text(json.dumps(payload, indent=2))
    print(f"  saved {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def process_ticker(ticker, force=False):
    print(f"\n[{ticker}]")
    if not force and is_fresh(ticker):
        print(f"  ↷ skipped (report is less than {CACHE_MAX_DAYS} days old)")
        return

    print(f"  scraping...")
    scraped_at = datetime.now(timezone.utc).isoformat()
    scraped = scrape_ticker(ticker)

    print(f"  generating report...")
    try:
        report = generate_report(ticker, scraped)
        save_report(ticker, report, scraped_at)
        print(f"  OK done")
    except Exception as e:
        print(f"  FAIL API error: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Run for a single ticker only")
    parser.add_argument("--force",  action="store_true", help="Ignore cache")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = load_primary_focus()
        print(f"Primary Focus: {len(tickers)} tickers — {', '.join(tickers)}")

    for i, ticker in enumerate(tickers):
        process_ticker(ticker, force=args.force)
        if i < len(tickers) - 1:
            time.sleep(INTER_TICKER_DELAY)

    print(f"\nDone. Reports in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
