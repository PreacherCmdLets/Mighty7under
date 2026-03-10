# Mighty7under · Market Command Centre

> Live market dashboard on **GitHub Pages**, auto-refreshed via **GitHub Actions** (Yahoo Finance) with **TradingView** embedded charts.

**Live URL:** `https://mashwin.github.io/Mighty7under`

---

## Project Structure

```
Mighty7under/
├── index.html                     ← Dashboard UI (7 tab pages)
├── data/
│   ├── snapshot.json              ← Auto-generated: prices & % changes
│   ├── events.json                ← Auto-generated: economic calendar
│   └── meta.json                  ← Auto-generated: build timestamp
├── scripts/
│   └── build_data.py              ← Data pipeline (yfinance → JSON)
├── requirements.txt
├── .github/workflows/
│   └── refresh_data.yml           ← GitHub Actions schedule
└── README.md
```

---

## One-Time GitHub Setup

**Step 1 — Push to GitHub**
```bash
git init
git remote add origin https://github.com/mashwin/Mighty7under.git
git add .
git commit -m "Initial commit"
git push -u origin main
```

**Step 2 — Enable GitHub Pages**
Settings → Pages → Source → **GitHub Actions** → Save

**Step 3 — Allow write permissions**
Settings → Actions → General → Workflow permissions → **Read and write** → Save

**Step 4 — Trigger first data fetch**
Actions tab → "Refresh Dashboard Data" → Run workflow → wait ~2 min → visit your site 🎉

---

## Auto-Refresh Schedule

- **9:35 AM ET** Mon–Fri (after market open)
- **4:35 PM ET** Mon–Fri (after market close)
- **Manual** anytime via Actions → Run workflow

---

## Dashboard Tabs

| Tab | Content |
|---|---|
| Macro | US Futures, VIX, Crypto, Metals, Energy, Yields, Global Indices |
| Equities | ETFs + S&P 500 Sectors ranked by 1W |
| Charts | TradingView interactive charts (SPX, NQ, BTC, Gold, DXY, VIX, Oil, Yields) |
| Breadth | Market internals + S&P 500 Heatmap |
| Calendar | TradingView live calendar + key events from events.json |
| Screener | TradingView stock / forex / crypto screeners |
| Position Sizer | Risk-based sizing with R:R calculator |

---

## Local Development

```bash
pip install -r requirements.txt
python scripts/build_data.py --out-dir data
python -m http.server 8000
# Open http://localhost:8000
```

---

## Customization

- **Add symbols:** Edit the `GROUPS` dict in `scripts/build_data.py`
- **Add events:** Edit `build_events()` in `scripts/build_data.py`
- **Change schedule:** Edit the cron in `.github/workflows/refresh_data.yml`
- **UI changes:** Edit `index.html` (self-contained single file)

---

*For informational purposes only. Not financial advice.*
