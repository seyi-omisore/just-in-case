"""Lists every button, link and field smaller than 48 by 48 pixels, or without a name.
Checks phone and desktop sizes, the smallest and largest text, and every screen.

Needs a browser driver, kept out of requirements.txt on purpose:
    pip install playwright          (uses the Google Chrome you already have)
Start the demo in one terminal:   python serve.py --demo --port 8011
Then run, in another:             python eval/audit_tap_targets.py
Optional first argument: the site address (default http://localhost:8011).
"""
import sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8011"
MEASURE = r"""
() => {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [tabindex]:not([tabindex="-1"])';
  const out = [];
  const nameOf = (e) => {
    const lab = e.getAttribute('aria-labelledby');
    if (lab) return lab.split(' ').map(i => (document.getElementById(i)||{}).textContent||'').join(' ').trim();
    return (e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].textContent) || e.textContent || e.getAttribute('title') || e.getAttribute('alt') || e.value || e.getAttribute('placeholder') || '').trim();
  };
  for (const e of document.querySelectorAll(sel)) {
    const cs = getComputedStyle(e), r = e.getBoundingClientRect();
    if (cs.display === 'none' || cs.visibility === 'hidden' || r.width === 0 || r.height === 0) continue;
    if (e.closest('[hidden]') || e.closest('dialog:not([open])')) continue;
    let box = r;
    if (e.type === 'checkbox' || e.type === 'radio') {
      const l = e.closest('label') || (e.id && document.querySelector(`label[for="${e.id}"]`));
      if (l) box = l.getBoundingClientRect();
    }
    const item = { tag: e.tagName.toLowerCase(), id: e.id, cls: String(e.className.baseVal ?? e.className).slice(0, 36),
      text: nameOf(e).replace(/\s+/g, ' ').slice(0, 34), w: Math.round(box.width), h: Math.round(box.height) };
    if (box.width < 47.5 || box.height < 47.5) item.small = true;
    if (!nameOf(e)) item.noname = true;
    if (e.tabIndex > 0) item.posTab = true;
    if (item.small || item.noname || item.posTab) out.push(item);
  }
  return out;
}
"""
found = {}
def record(tag, page, label):
    for it in page.evaluate(MEASURE):
        key = (it["tag"], it["id"], it["cls"], it["text"])
        found.setdefault(key, {**it, "where": set()})["where"].add(label)

def drive_example(page, label):
    """Play Ruth's example, saying yes to every card, measuring along the way."""
    for _ in range(70):
        record(page, page, label) if False else record(None, page, label)
        # tick every box on a checklist, then continue
        boxes = page.locator(".slip-checks input[type=checkbox]:not([disabled])")
        for i in range(boxes.count()):
            try: boxes.nth(i).check(timeout=500)
            except Exception: pass
        btn = page.locator(".slip-actions .btn-primary:visible").first
        if btn.count():
            try: btn.click(timeout=1500)
            except Exception: pass
        page.wait_for_timeout(900)
        if page.locator(".tag").count() >= 3 and not page.locator(".slip-actions").count() and page.locator("#next-step:not([hidden])").count():
            page.wait_for_timeout(2500)
            break

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    for width, height, fs in ((390, 800, 18), (390, 800, 27), (1280, 900, 20)):
        ctx = browser.new_context(viewport={"width": width, "height": height})
        ctx.add_init_script(f"try{{localStorage.setItem('jic-size','{fs}')}}catch(e){{}}")
        page = ctx.new_page()
        tag = f"{width}px/{fs}"
        for hashname in ("home", "help", "about", "features", "privacy"):
            page.goto(f"{BASE}/#{hashname}"); page.wait_for_timeout(400)
            record(None, page, f"{tag} {hashname}")
        page.goto(f"{BASE}/#home"); page.click("#more-menu summary"); record(None, page, f"{tag} menu open")
        page.goto(f"{BASE}/#plan"); page.wait_for_timeout(1200)
        record(None, page, f"{tag} plan start")
        page.click("#view-full"); record(None, page, f"{tag} plan full view"); page.click("#view-simple")
        page.goto(f"{BASE}/?example=1#plan"); page.wait_for_timeout(1500)
        drive_example(page, f"{tag} example")
        # open every plan line and details link
        for h in page.locator(".tag-head").all():
            try: h.click(timeout=800)
            except Exception: pass
        for d in page.locator("summary").all():
            try:
                if d.is_visible(): d.click(timeout=500)
            except Exception: pass
        record(None, page, f"{tag} everything open")
        page.click("#view-full"); page.wait_for_timeout(300); record(None, page, f"{tag} full view after plan")
        page.goto(f"{BASE}/share/anything"); page.wait_for_timeout(500); record(None, page, f"{tag} share page")
        record(None, page, f'{tag} share page') 
        ctx.close()
    browser.close()
rows = sorted(found.values(), key=lambda r: (r["tag"], r["cls"], r["text"]))
for r in rows:
    flags = ",".join(k for k in ("small", "noname", "posTab") if r.get(k))
    print(f'{flags:12} {r["tag"]:8} {r["w"]:>4}x{r["h"]:<4} #{r["id"]} .{r["cls"]} "{r["text"]}"  [{"; ".join(sorted(r["where"]))[:70]}]')
print("TOTAL", len(rows))
