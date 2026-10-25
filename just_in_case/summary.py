"""The "My readiness summary" page. The person chooses to create it.

Built by code from the plan only: dates, review count, and what is in place for each
situation. It lists a support as "in place" only after the person said "Yes, add this".
Nothing here is stored. The page is made fresh each time it is opened.
"""

import html
import re
from datetime import date

from . import reviews
from .directory import Directory
from .safeguards import EMERGENCY_LINE, first_name
from .state import NEEDS, PlanState

# Printed at the top of every summary. It is not one of the parts the person can untick.
SELF_REPORT_NOTE = "This is a self reported summary of my planning. It is not a medical or functional assessment."

# Black icons drawn inline, so the page prints without Wi-Fi. Every icon sits beside words.
ICONS = {
    "calendar": '<path d="M7 2h2v2h6V2h2v2h3a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3zM5 9v10h14V9zm2 2h4v4H7z"/>',
    "check": '<path d="m9.5 16.2-4-4L4 13.7l5.5 5.5L20 8.7l-1.5-1.5z"/>',
    "dash": '<path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zM7 11v2h10v-2z"/>',
    "wait": '<path d="M6 2h12v5l-4 5 4 5v5H6v-5l4-5-4-5zm2 2v2.3l4 5 4-5V4z"/>',
    "phone": '<path d="M6.6 3h3l1.5 4.5-2 1.5a11 11 0 0 0 5.9 5.9l1.5-2L21 14.4v3A2.6 2.6 0 0 1 18.4 20 15.4 15.4 0 0 1 4 5.6 2.6 2.6 0 0 1 6.6 3z"/>',
    "person": '<circle cx="12" cy="7" r="4"/><path d="M4 21a8 8 0 0 1 16 0z"/>',
    "key": '<path d="M14.5 3a6.5 6.5 0 0 0-6.2 8.4L3 16.7V21h4.3v-2.2h2.2v-2.2h2.2l1-1A6.5 6.5 0 1 0 14.5 3zm2 3a1.7 1.7 0 1 1 0 3.4 1.7 1.7 0 0 1 0-3.4z"/>',
    "card": '<path d="M4 4h16v16H4zm2 2v12h12V6zm2 2h8v2H8zm0 4h8v2H8zm0 4h5v2H8z"/>',
    "home": '<path d="M3 11 12 3l9 8v10h-6v-6H9v6H3z"/>',
    "list": '<path d="M4 5h16v2H4zm0 6h16v2H4zm0 6h16v2H4z"/>',
}


def short(name: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", name or "").strip()


def readiness(state: PlanState, directory: Directory, today: date) -> dict:
    """Everything the page shows, as plain data (so it can be tested)."""
    helpers = [first_name(h) for n in state.helper_notes for h in n["helpers"] if first_name(h)]  # first names only
    by_need = {i.need: i for i in state.items}
    situations = []
    for need in state.confirmed_needs:
        item = by_need.get(need)
        supports = []
        if item and item.route != "blank":
            ref = directory.by_id.get(item.ref_id or "", {})
            if not item.user_ok:
                supports.append({"kind": "waiting", "icon": "wait", "label": "Waiting for your OK"})
            elif item.route == "service":
                supports.append({"kind": "service", "icon": "phone", "label": f"Service lined up: {short(ref.get('name', item.label))}"})
            elif item.route == "preparation":
                supports.append({"kind": "prep", "icon": "key" if item.ref_id == "prep_key_lockbox" else "check",
                                 "label": f"Ready: {short(ref.get('name', item.label))}"})
            else:
                names = " and ".join(helpers) if helpers else "someone you chose"
                supports.append({"kind": "helper", "icon": "person", "label": f"Helper chosen: {names}"})
        situations.append({"need": need, "label": NEEDS[need],
                           "covered": bool(item and item.route != "blank" and item.user_ok),
                           "supports": supports})
    in_place = {i.ref_id for i in state.items if i.user_ok and i.route != "blank"}
    tasks = state.helper_tasks
    confirmed = sum(1 for t in tasks if t.get("person") and t.get("confirmed"))
    last = reviews.parse(state.reviews[-1]) if state.reviews else None
    started = reviews.parse(state.started) or today
    return {
        "started": reviews.words(started), "last_review": reviews.words(last) if last else "Not yet",
        "review_count": len(state.reviews), "created": reviews.words(today),
        "situations": situations,
        "covered": sum(1 for s in situations if s["covered"]), "total": len(situations),
        "across": {
            "helpers_confirmed": confirmed, "helper_tasks": len(tasks),
            "key_lockbox": "prep_key_lockbox" in in_place,
            "fridge_card": bool(state.card_path),
        },
    }


def _svg(name: str) -> str:
    return f'<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'


def render_summary_html(state: PlanState, directory: Directory, today: date | None = None) -> str:
    e = html.escape
    d = readiness(state, directory, today or date.today())
    rows = []
    for s in d["situations"]:
        if s["supports"]:
            chips = "".join(f'<li class="chip {c["kind"]}">{_svg(c["icon"])}<span>{e(c["label"])}</span></li>' for c in s["supports"])
        else:
            chips = f'<li class="chip none">{_svg("dash")}<span>Still to plan</span></li>'
        mark = _svg("check") if s["covered"] else _svg("dash")
        word = "In place" if s["covered"] else "Not yet"
        rows.append(f'<li class="sit"><div class="sit-head">{mark}<strong>{e(s["label"])}</strong>'
                    f'<span class="state">{word}</span></div><ul class="chips">{chips}</ul></li>')
    a = d["across"]
    helpers = (f'{a["helpers_confirmed"]} of {a["helper_tasks"]} helper tasks confirmed'
               if a["helper_tasks"] else "No helper tasks yet")
    across = [
        ("person", "Helpers confirmed", helpers, a["helpers_confirmed"] > 0),
        ("key", "Key lockbox", "In place" if a["key_lockbox"] else "Not in my plan", a["key_lockbox"]),
        ("card", "Fridge card", "Made" if a["fridge_card"] else "Not made yet", a["fridge_card"]),
    ]
    parts = ("helpers", "lockbox", "card")  # the names the checkboxes use
    across_html = "".join(f'<li class="chip" data-part="{part}">{_svg(icon)}<span><strong>{e(name)}:</strong> {e(text)}</span></li>'
                          for part, (icon, name, text, _) in zip(parts, across))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="color-scheme" content="light only"><meta name="darkreader-lock">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My readiness summary</title>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
 @page {{ size: letter; margin: 0.5in; }}
 body {{ font-family: "Atkinson Hyperlegible", Verdana, Arial, sans-serif; font-size: 15pt; line-height: 1.35;
        color: #000; background: #fff; max-width: 7.5in; margin: 0.3in auto; padding: 0 0.2in; }}
 .banner {{ background: #000; color: #fff; text-align: center; font-weight: 700; font-size: 22pt; padding: 0.1in; border-radius: 10px; }}
 h1 {{ font-size: 22pt; margin: 0.15in 0 0.04in; }}
 h2 {{ font-size: 15pt; margin: 0.04in 0; display: flex; align-items: center; gap: 0.08in; }}
 .note {{ border: 4px solid #000; border-radius: 10px; padding: 0.08in 0.16in; margin: 0.06in 0 0.1in;
         font-size: 16pt; font-weight: 700; }}
 .made {{ margin: 0 0 0.1in; font-size: 12pt; }}
 section {{ border: 3px solid #000; border-radius: 10px; padding: 0.06in 0.16in 0.1in; margin-top: 0.12in; }}
 ul {{ list-style: none; margin: 0; padding: 0; }}
 .ic {{ width: 18pt; height: 18pt; flex: none; fill: #000; }}
 .facts li, .chip {{ display: flex; align-items: center; gap: 0.1in; padding: 0.04in 0; }}
 .sit {{ padding: 0.05in 0; border-top: 1px solid #999; }}
 .sit:first-child {{ border-top: 0; }}
 .sit-head {{ display: flex; align-items: center; gap: 0.1in; }}
 .state {{ margin-left: auto; font-weight: 700; border: 2px solid #000; border-radius: 999px; padding: 0 0.1in; font-size: 12pt; }}
 .chips {{ margin: 0.02in 0 0 0.3in; font-size: 13pt; }}
 .foot {{ margin-top: 0.12in; font-weight: 700; }}
 [hidden] {{ display: none !important; }}
 .choose fieldset {{ border: 3px solid #000; border-radius: 10px; padding: 0.06in 0.16in 0.1in; margin: 0.1in 0 0; }}
 .choose legend {{ font-weight: 700; padding: 0 0.06in; }}
 .choose label {{ display: flex; align-items: center; gap: 0.1in; min-height: 48px; cursor: pointer; }}
 .choose input {{ width: 24px; height: 24px; accent-color: #1F6F78; }}
 .how {{ font-size: 12pt; margin: 0.06in 0 0; }}
 .print[disabled] {{ opacity: .5; cursor: not-allowed; }}
 .print {{ display: block; min-height: 48px; margin: 0.25in auto 0; font: inherit; font-weight: 700; padding: 0.1in 0.35in;
          border: 0; border-radius: 10px; background: #1F6F78; color: #fff; cursor: pointer; }}
 @media print {{ .print, .choose {{ display: none; }} body {{ margin: 0; }} }}
</style></head>
<body>
 <div class="banner">{e(EMERGENCY_LINE.upper())}</div>
 <h1>My readiness summary</h1>
 <p class="note" role="note">{e(SELF_REPORT_NOTE)}</p>
 <p class="made">Made on {e(d["created"])}. It shows what is in my plan today.</p>
 <form class="choose" onsubmit="return false">
  <fieldset><legend>Choose what to include</legend>
   <label><input type="checkbox" value="dates" checked> Dates and reviews</label>
   <label><input type="checkbox" value="situations" checked> Each situation</label>
   <label><input type="checkbox" value="helpers" checked> Helpers confirmed</label>
   <label><input type="checkbox" value="lockbox" checked> Key lockbox</label>
   <label><input type="checkbox" value="card" checked> Fridge card</label>
  </fieldset>
  <p class="how">Then tap Print. To save a PDF, pick "Save as PDF" in the print window. The 911 line is always included.</p>
  <p id="pick-status" class="how" role="status"></p>
 </form>
 <section data-part="dates"><h2>{_svg("calendar")}My plan</h2><ul class="facts">
  <li>{_svg("home")}<span><strong>Started:</strong> {e(d["started"])}</span></li>
  <li>{_svg("check")}<span><strong>Last reviewed:</strong> {e(d["last_review"])}</span></li>
  <li>{_svg("list")}<span><strong>Times reviewed:</strong> {d["review_count"]}</span></li>
 </ul></section>
 <section data-part="situations"><h2>{_svg("check")}By situation: {d["covered"]} of {d["total"]} in place</h2>
  <ul>{"".join(rows) or "<li>No situations chosen yet.</li>"}</ul></section>
 <section id="across"><h2>{_svg("list")}Across my plan</h2><ul class="facts">{across_html}</ul></section>
 <p class="foot">{e(EMERGENCY_LINE)}</p>
 <button class="print" id="print" onclick="window.print()">Print my summary</button>
<script>
 // Unticked parts are hidden on screen and left off the printout. Nothing leaves this page.
 const boxes = [...document.querySelectorAll(".choose input")];
 function apply() {{
  let any = false;
  for (const b of boxes) {{
   any = any || b.checked;
   document.querySelectorAll('[data-part="' + b.value + '"]').forEach((el) => (el.hidden = !b.checked));
  }}
  document.getElementById("across").hidden = !document.querySelector("#across [data-part]:not([hidden])");
  document.getElementById("print").disabled = !any;
  document.getElementById("pick-status").textContent = any ? "" : "Tick at least one part to print.";
 }}
 boxes.forEach((b) => b.addEventListener("change", apply));
 apply();
</script>
</body></html>"""
