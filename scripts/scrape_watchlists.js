const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const WATCHLISTS = [
  { id: '175895814', url: 'https://www.tradingview.com/watchlists/175895814/' },
  { id: '197124576', url: 'https://www.tradingview.com/watchlists/197124576/' },
  { id: '170416987', url: 'https://www.tradingview.com/watchlists/170416987/' },
];

// Matches EXCHANGE:TICKER including dots and slashes (e.g. NYSE:BRK.A, NYSE:ALB/PA)
const SYMBOL_RE = /^[A-Z]{1,10}:[A-Z0-9][A-Z0-9./]{0,11}$/;

function extractSymbols(obj, found = new Set()) {
  if (typeof obj === 'string') {
    if (SYMBOL_RE.test(obj)) found.add(obj);
  } else if (Array.isArray(obj)) {
    obj.forEach(item => extractSymbols(item, found));
  } else if (obj && typeof obj === 'object') {
    Object.values(obj).forEach(val => extractSymbols(val, found));
  }
  return found;
}

async function scrapeWatchlist(browser, watchlist) {
  const page = await browser.newPage();
  const capturedSymbols = new Set();
  let watchlistName = `Watchlist ${watchlist.id}`;
  const responsePromises = [];

  await page.setExtraHTTPHeaders({ 'Accept-Language': 'en-US,en;q=0.9' });

  page.on('response', (response) => {
    const ct = response.headers()['content-type'] || '';
    if (!ct.includes('json')) return;
    const p = response.json().then(json => {
      if (json.name && typeof json.name === 'string' && json.name.length > 0) {
        watchlistName = json.name;
      }
      extractSymbols(json, capturedSymbols);
    }).catch(() => {});
    responsePromises.push(p);
  });

  try {
    await page.goto(watchlist.url, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(4000);
    await Promise.allSettled(responsePromises);

    // DOM fallback
    if (capturedSymbols.size === 0) {
      const domSymbols = await page.evaluate(() => {
        const results = [];
        ['data-symbol', 'data-symbol-full', 'data-name'].forEach(attr => {
          document.querySelectorAll(`[${attr}]`).forEach(el => results.push(el.getAttribute(attr)));
        });
        return results;
      });
      domSymbols.filter(s => s && SYMBOL_RE.test(s)).forEach(s => capturedSymbols.add(s));
    }

    const title = await page.title();
    if (title && !title.toLowerCase().startsWith('tradingview')) {
      watchlistName = title.replace(/\s*[-|].*$/, '').trim() || watchlistName;
    }
  } catch (err) {
    console.error(`  Error scraping ${watchlist.url}: ${err.message}`);
  }

  await page.close();

  return {
    id: watchlist.id,
    name: watchlistName,
    url: watchlist.url,
    symbols: Array.from(capturedSymbols).sort(),
    lastUpdated: new Date().toISOString(),
  };
}

async function main() {
  console.log('Launching browser...');
  const browser = await chromium.launch({ headless: true });
  const results = [];

  for (const watchlist of WATCHLISTS) {
    console.log(`Scraping watchlist ${watchlist.id}...`);
    const result = await scrapeWatchlist(browser, watchlist);
    console.log(`  Found ${result.symbols.length} symbols`);
    results.push(result);
  }

  await browser.close();

  const dataDir = path.join(__dirname, '..', 'data');
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(path.join(dataDir, 'watchlists.json'), JSON.stringify(results, null, 2));
  console.log('Saved to data/watchlists.json');
}

main().catch(err => { console.error(err); process.exit(1); });
