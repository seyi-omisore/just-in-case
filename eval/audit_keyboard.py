"""Tabs through every screen: each stop must show a focus ring and be on screen.
Also checks that the page never scrolls sideways at the largest text size.

Needs a browser driver, kept out of requirements.txt on purpose:
    pip install playwright          (uses the Google Chrome you already have)
Start the demo in one terminal:   python serve.py --demo --port 8011
Then run, in another:             python eval/audit_keyboard.py
Optional first argument: the site address (default http://localhost:8011).
"""
import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8011"
FOCUS = r"""() => { const e = document.activeElement; if (!e || e === document.body) return null;
  if (!e.dataset.auditId) e.dataset.auditId = String(window.__aid = (window.__aid || 0) + 1);
  const cs = getComputedStyle(e), r = e.getBoundingClientRect();
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2) || (cs.boxShadow && cs.boxShadow !== 'none');
  const nm = (e.getAttribute('aria-label') || e.textContent || e.value || e.placeholder || '').trim().replace(/\s+/g,' ').slice(0,30);
  return { tag: e.tagName.toLowerCase(), id: e.id, uid: e.dataset.auditId, name: nm, ring: !!ring, onscreen: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth,
           expanded: e.getAttribute('aria-expanded'), pressed: e.getAttribute('aria-pressed') }; }"""
bad = []
def tab_through(page, label, limit=70):
    seen = set()
    for _ in range(limit):
        page.keyboard.press("Tab"); f = page.evaluate(FOCUS)
        if not f: continue
        key = f["uid"]
        if key in seen: break
        seen.add(key)
        if not f["ring"] or not f["onscreen"]: bad.append((label, f))
    return len(seen)
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    for width, fs in ((390, 27), (1280, 20)):
        ctx = b.new_context(viewport={"width": width, "height": 800})
        ctx.add_init_script(f"try{{localStorage.setItem('jic-size','{fs}')}}catch(e){{}}")
        page = ctx.new_page()
        for h in ("home", "help", "about", "features", "privacy", "plan"):
            page.goto(f"{BASE}/#{h}"); page.wait_for_timeout(500)
            n = tab_through(page, f"{width}/{fs} {h}")
            over = page.evaluate("document.documentElement.scrollWidth - innerWidth")
            print(f"{width}/{fs} #{h:9} focus stops: {n:3}  sideways overflow: {over}px")
        # keyboard-only: open the menu, switch view, use a starter and Next
        page.goto(f"{BASE}/#plan"); page.wait_for_timeout(800)
        page.keyboard.press("Tab")  # skip link
        page.focus("#view-full"); page.keyboard.press("Enter")
        print("  view switch by keyboard ->", page.get_attribute("#view-full", "aria-pressed"))
        page.focus("#view-simple"); page.keyboard.press("Space")
        page.focus(".starter"); page.keyboard.press("Enter"); page.wait_for_timeout(2500)
        print("  starter by keyboard -> pages shown:", page.locator("#step-count").inner_text(), "| announce:", page.locator("#step-announce").inner_text()[:50])
        ctx.close()
    b.close()
print("PROBLEMS:", len(bad))
for lab, f in bad[:25]: print(" ", lab, f)
