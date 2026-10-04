"""PS Auction collector via Playwright (headless Chromium).

PS Auction sits behind an AWS WAF JS challenge, so plain HTTP returns only the
challenge page. A real headless browser solves the challenge and renders the
auction list, from which we read each item (object number, link, current highest
bid from the PRICE display — not the bid form, which is the next suggested bid).

Resource-bounded: one browser, a capped number of auctions per run (every ~4h).
"""
import os
import re
import logging
from datetime import datetime, timezone, timedelta

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/pw-browsers")

logger = logging.getLogger("ps_playwright")

INDEX_URL = "https://psauction.se/auctions"
MAX_AUCTIONS = int(os.environ.get("PS_MAX_AUCTIONS", "6"))
MAX_ITEMS = int(os.environ.get("PS_MAX_ITEMS", "500"))

CARD_JS = r"""() => {
  const out = {};
  for (const link of document.querySelectorAll('a[href*="/item/view/"]')) {
    const id = (link.href.match(/\/item\/view\/(\d+)/)||[])[1];
    if (!id || out[id]) continue;
    const t = (link.innerText||'');
    const lines = t.split('\n').map(s=>s.trim()).filter(Boolean);
    const isLabel = s => /^(Reservationspris|Inget reservation|Utan reservation|Plats:|Tid kvar:|Slutar|Avslutad|Vinnande|\d+\s*bud|Inga bud)/i.test(s)
                         || /^\d[\d\s\u00a0]*SEK/.test(s) || /^\d+$/.test(s) || s.length < 3;
    let title = null;
    for (const l of lines) { if (!isLabel(l)) { title = l; break; } }
    const bidM = t.match(/([\d][\d\s\u00a0]*)\s*SEK/);
    const noBids = /Inga bud/i.test(t);
    const img = link.querySelector('img');
    out[id] = {
      item_id: id,
      title: title,
      image: img ? img.src : null,
      current_bid: bidM ? Number(bidM[1].replace(/[\s\u00a0]/g,'')) : null,
      bid_status: noBids ? 'inga_bud' : (bidM ? 'bud_finns' : 'okant'),
      reserve_met: /Reservationspris uppn/i.test(t) ? true : (/(Inget|Utan) reservation/i.test(t) ? null : null),
      location: (t.match(/Plats:\s*([^\n]+)/)||[])[1] || null,
      time_left: (t.match(/Tid kvar:\s*([^\n]+)/)||[])[1] || null,
      ended: /Avslutad/i.test(t),
      ends_exact: (t.match(/(\d{4}-\d{2}-\d{2})[\sT]+(\d{2}:\d{2})/)||[]).slice(1).join(' ') || null,
      url: link.href,
    };
  }
  return Object.values(out);
}"""


def _parse_exact(s):
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", s.strip())
    if not m:
        return None
    return f"{m.group(1)}T{m.group(2)}:00+02:00"


def _ends_from_timeleft(s):
    if not s or re.search(r"avslut", s, re.I):
        return None
    d = int((re.search(r"(\d+)\s*d", s) or [0, 0])[1]) if re.search(r"(\d+)\s*d", s) else 0
    h = int((re.search(r"(\d+)\s*h", s) or [0, 0])[1]) if re.search(r"(\d+)\s*h", s) else 0
    m = int((re.search(r"(\d+)\s*m", s) or [0, 0])[1]) if re.search(r"(\d+)\s*m", s) else 0
    sec = int((re.search(r"(\d+)\s*s", s) or [0, 0])[1]) if re.search(r"(\d+)\s*s(?!\w)", s) else 0
    if d == 0 and h == 0 and m == 0 and sec == 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=d, hours=h, minutes=m, seconds=sec)).isoformat()


async def collect():
    """Return list of PS Auction item records. Empty list on failure."""
    from playwright.async_api import async_playwright
    results, seen = [], set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page()
            await page.goto(INDEX_URL, wait_until="domcontentloaded", timeout=45000)
            await page.locator('a[href*="/auction/"]').first.wait_for(timeout=30000)
            auction_urls = await page.evaluate(
                r"""() => [...new Set([...document.querySelectorAll('a[href*="/auction/"]')]
                    .map(a => a.href.match(/\/auction\/\d+\/[^?#]+/)) .filter(Boolean)
                    .map(m => 'https://psauction.se' + m[0]))]"""
            )
            auction_urls = auction_urls[:MAX_AUCTIONS]
            for au in auction_urls:
                try:
                    await page.goto(au, wait_until="domcontentloaded", timeout=45000)
                    await page.locator('a[href*="/item/view/"]').first.wait_for(timeout=25000)
                    # Select 100 per page if offered
                    try:
                        link100 = page.get_by_role("link", name="100", exact=True)
                        if await link100.count():
                            await link100.first.click()
                            await page.wait_for_timeout(1500)
                    except Exception:
                        pass
                    await page.wait_for_timeout(1500)
                    items = await page.evaluate(CARD_JS)
                    for it in items:
                        if it["item_id"] in seen or it.get("ended"):
                            continue
                        seen.add(it["item_id"])
                        it["source"] = "PS Auction"
                        it["ends_at"] = _parse_exact(it.get("ends_exact")) or _ends_from_timeleft(it.get("time_left"))
                        it["current_bid"] = it.get("current_bid") or 0
                        results.append(it)
                    if len(results) >= MAX_ITEMS:
                        break
                except Exception as e:
                    logger.warning("PS auction page failed %s: %s", au, e)
                    continue
        finally:
            await browser.close()
    return results
