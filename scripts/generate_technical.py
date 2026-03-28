"""
generate_technical.py  —  Mighty7under AI Technical Chart Analysis
===================================================================
For each ticker in the "Primary Focus" watchlist (on-demand only):
  1. Takes a weekly chart screenshot via Playwright (TradingView)
  2. Fetches price history for MA/pattern context (StockAnalysis)
  3. Sends chart image + price data + analysis prompt to Claude vision API
  4. Saves 10-section technical report to data/research/<TICKER>_technical.json

Usage:
  python scripts/generate_technical.py              # all Primary Focus tickers
  python scripts/generate_technical.py --ticker AAPL  # single ticker

Triggered manually via GitHub Actions workflow_dispatch.
Requires: ANTHROPIC_API_KEY environment variable
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────

WATCHLIST_FILE      = Path("data/watchlists.json")
OUTPUT_DIR          = Path("data/research")
SCREENSHOT_DIR      = Path("data/research/.charts")
CLAUDE_MODEL        = "claude-sonnet-4-6"
INTER_TICKER_DELAY  = 5

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_primary_focus():
    data = json.loads(WATCHLIST_FILE.read_text())
    wl = next((w for w in data if w["name"] == "Primary Focus"), None)
    if not wl:
        print("ERROR: 'Primary Focus' watchlist not found")
        sys.exit(1)
    return [s.split(":")[1] for s in wl["symbols"]]


def capture_chart(ticker):
    """Screenshot TradingView weekly chart. Returns PNG bytes or None."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{ticker}_weekly.png"
    url = f"https://www.tradingview.com/chart/?symbol={ticker}&interval=W"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 800})
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(5)  # wait for chart canvas to render
            page.screenshot(path=str(path), full_page=False)
            browser.close()

        print(f"  ✓ chart screenshot saved")
        return path.read_bytes()

    except Exception as e:
        print(f"  ✗ screenshot failed: {e}")
        return None


def fetch_price_history(ticker):
    """Fetch recent monthly price history for MA/pattern context."""
    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/history/?p=monthly"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            text = page.evaluate("document.body.innerText.slice(0,2500)")
            browser.close()
        print(f"  ✓ price history")
        return text
    except Exception as e:
        print(f"  ✗ price history failed: {e}")
        return "N/A"


def fetch_statistics(ticker):
    """Fetch key statistics (MAs, RSI, 52w range) for technical context."""
    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/statistics/"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            text = page.evaluate("document.body.innerText.slice(0,2500)")
            browser.close()
        print(f"  ✓ statistics")
        return text
    except Exception as e:
        print(f"  ✗ statistics failed: {e}")
        return "N/A"


TECHNICAL_FRAMEWORK = """
**Trend Classification**
- Uptrend: HH+HL, price above MAs, MAs bullish order (20w>50w>200w)
- Downtrend: LH+LL, price below MAs, MAs bearish order
- Sideways: oscillates between S/R, no clear HH/HL or LH/LL
- Exhaustion: fading volume on new highs/lows, extended distance from MAs, reversal candles

**Support & Resistance**
- Valid: 2–3+ touches, volume spikes, confluence with MAs/round numbers
- Role reversal: broken support→resistance, broken resistance→support
- Strength: Strong=3+ touches long-standing. Weak=1–2 touches recent

**Moving Averages (Weekly)**
- 20w MA: ~4 months. 50w MA: ~1 year. 200w MA: ~4 years
- Bullish: 20w>50w>200w all rising. Bearish: 20w<50w<200w all falling
- Converging MAs → significant move incoming
- Golden Cross: 20w crosses above 50w. Death Cross: 20w crosses below 50w

**Volume**
- Rising price + Rising vol → healthy uptrend ✓
- Falling price + Rising vol → healthy downtrend ✓
- Rising price + Falling vol → weak rally ⚠
- Falling price + Falling vol → exhaustion ⚠
- Bullish divergence: new lows + declining vol. Bearish: new highs + declining vol

**Patterns**
- Bullish reversal: Hammer, Bullish Engulfing, Morning Star, Double/Triple Bottom
- Bearish reversal: Shooting Star, Bearish Engulfing, Evening Star, Double/Triple Top
- Continuation bullish: Bull Flag, Ascending Triangle
- Continuation bearish: Bear Flag, Descending Triangle

**Probability Framework**
- High (50–70%): aligned with trend, multiple confirming factors, clear invalidation
- Medium (25–45%): requires trend change or major breakout
- Low (5–20%): contrary to most factors
- Probabilities must sum to 100%. Each scenario needs specific invalidation price.
"""


def build_prompt(ticker, stats_text, history_text, has_image):
    image_note = "Analyze the attached weekly chart image." if has_image else \
        "No chart image available — base analysis on the statistical data below."

    return f"""You are a professional technical analyst. {image_note}

Use the complete technical analysis framework below. Analysis must be based strictly on observable chart/price data — do not incorporate fundamental data.

## TECHNICAL ANALYSIS FRAMEWORK
{TECHNICAL_FRAMEWORK}

## STATISTICS DATA (50-day MA, 200-day MA, RSI, 52w range, volume)
{stats_text}

## PRICE HISTORY (recent monthly candles)
{history_text}

## ANALYSIS CHECKLIST (complete all 8 before writing)
✓ Trend: direction, strength, duration, HH/HL or LH/LL structure
✓ Support: 3 key levels with significance
✓ Resistance: 3 key levels with significance
✓ Moving Averages: position vs 20w/50w/200w, slope, crossovers
✓ Volume: trend, key spikes, confirmation or divergence
✓ Patterns: any forming or completing
✓ Scenarios: 2–4 with probabilities summing to 100%
✓ Invalidation: specific price for each scenario

## REPORT — produce in full, no placeholders:

---

# **{ticker} — TECHNICAL ANALYSIS**

**Ticker**: {ticker} | **Timeframe**: Weekly | **Date**: {datetime.now(timezone.utc).strftime('%b %d, %Y')}

#### 1. Chart Overview
[Current price, overall chart character, instrument type]

#### 2. Trend Analysis
- **Direction**: [Uptrend / Downtrend / Sideways]
- **Strength**: [Strong / Moderate / Weak]
- **Duration**: [How long]
[HH/HL or LH/LL structure detail. Exhaustion signals if any.]

#### 3. Support & Resistance

**Key Support:**
1. **$X** — [why significant]
2. **$X** — [why significant]
3. **$X** — [why significant]

**Key Resistance:**
1. **$X** — [why significant]
2. **$X** — [why significant]
3. **$X** — [why significant]

[Role reversal notes, confluence zones]

#### 4. Moving Average Analysis
- Price vs 20w MA: [Above/Below/At] — [interpretation]
- Price vs 50w MA: [Above/Below/At] — [interpretation]
- Price vs 200w MA: [Above/Below/At] — [interpretation]
- **Alignment**: [Bullish/Bearish/Neutral/Mixed]
- **Slope**: [Rising/Falling/Flat]
- **Crossovers**: [Recent or pending golden/death cross]
- **MA as S/R**: [Which MAs acting as dynamic support/resistance]

#### 5. Volume Analysis
[Volume trend 10–15 weeks]
- [Key observation 1]
- [Key observation 2]
- [Key observation 3]
**Verdict**: [Confirms / Contradicts / Neutral]

#### 6. Chart Patterns & Price Action
- [Pattern name]: [description, location, implication]
- [Pattern name]: [description]
[Recent candle behavior, breakouts, rejections]

#### 7. Market Assessment
[Synthesis of all elements]
1. [Most important observation]
2. [Second observation]
3. [Third observation]

#### 8. Scenario Analysis

**Scenario 1: [Name] — XX%**
- Description | Supporting factors (3) | Target: $X | Invalidation: $X

**Scenario 2: [Name] — XX%**
- Description | Supporting factors (3) | Target: $X | Invalidation: $X

**Scenario 3: [Name] — XX%**
- Description | Supporting factors (2) | Target: $X | Invalidation: $X

**Scenario 4 (optional): [Name] — XX%**
- Description | Target: $X | Invalidation: $X

#### 9. Technical Summary
- **Most likely**: [highest probability scenario]
- **Critical Support**: $X | **Critical Resistance**: $X
- **Risk considerations**: [caveats, limitations]

---

*Generated by Mighty7under AI Research — Technical*
"""


def generate_report(ticker, stats_text, history_text, chart_bytes):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt_text = build_prompt(ticker, stats_text, history_text, chart_bytes is not None)

    content = []

    if chart_bytes:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(chart_bytes).decode("utf-8")
            }
        })

    content.append({"type": "text", "text": prompt_text})

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}]
    )
    return message.content[0].text


def update_manifest(ticker, report_type, date_str):
    manifest_path = OUTPUT_DIR / "index.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            manifest = {}
    if ticker not in manifest:
        manifest[ticker] = {}
    dates = manifest[ticker].get(report_type, [])
    if date_str not in dates:
        dates = sorted([date_str] + dates, reverse=True)
    manifest[ticker][report_type] = dates
    manifest_path.write_text(json.dumps(manifest, indent=2))


def save_report(ticker, report_text):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    payload = {
        "ticker": ticker,
        "type": "technical",
        "generated_at": now.isoformat(),
        "report": report_text
    }
    data = json.dumps(payload, indent=2)

    # Save dated copy
    dated_dir = OUTPUT_DIR / date_str
    dated_dir.mkdir(parents=True, exist_ok=True)
    (dated_dir / f"{ticker}_technical.json").write_text(data)

    # Overwrite latest
    (OUTPUT_DIR / f"{ticker}_technical.json").write_text(data)

    update_manifest(ticker, "technical", date_str)
    print(f"  saved {date_str}/{ticker}_technical.json")


# ── Main ──────────────────────────────────────────────────────────────────────

def process_ticker(ticker):
    print(f"\n[{ticker}]")

    print(f"  capturing chart...")
    chart_bytes = capture_chart(ticker)

    print(f"  fetching stats + history...")
    with sync_playwright() as p:
        pass  # playwright already used above; fetch separately

    stats_text   = fetch_statistics(ticker)
    history_text = fetch_price_history(ticker)

    print(f"  generating report...")
    try:
        report = generate_report(ticker, stats_text, history_text, chart_bytes)
        save_report(ticker, report)
        print(f"  ✓ done")
    except Exception as e:
        print(f"  ✗ API error: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Run for a single ticker only")
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
        process_ticker(ticker)
        if i < len(tickers) - 1:
            time.sleep(INTER_TICKER_DELAY)

    print(f"\nDone. Reports in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
