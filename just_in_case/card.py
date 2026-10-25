"""Builds the printable "If something happens" refrigerator card.

The card is assembled by code from (a) what the user typed and (b) plan items
that already passed the tool checks. The model cannot add free text to it, so
it cannot slip in medical advice or a service that is not in the directory.
"""

import html
import re
from datetime import date, timedelta
from pathlib import Path

from . import reviews
from .config import OUTPUT_DIR
from .directory import Directory
from .safeguards import CRISIS_LINE, EMERGENCY_LINE
from .state import NEEDS, PlanState


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "card"


def build_card_lines(card: dict, state: PlanState, directory: Directory, today: date) -> list[str]:
    review_by = reviews.review_by(state.reviews, today)
    lines = [
        f"IF SOMETHING HAPPENS: {card['name']}" if card.get("name") else "IF SOMETHING HAPPENS",
        EMERGENCY_LINE.upper(),
        CRISIS_LINE,
        "",
    ]
    # Only sections the user gave us. Nothing is assumed or filled in.
    if card.get("contacts"):
        lines += ["PEOPLE TO CONTACT"]
        for c in card["contacts"]:
            rel = f" ({c['relationship']})" if c.get("relationship") else ""
            lines.append(f"  {c['name']}{rel}: {c['phone']}")
        lines.append("")

    if card.get("medications"):
        lines += ["MY MEDICATIONS (as I listed them)"]
        lines += [f"  {m}" for m in card["medications"]]
        lines += ["  Questions about medications go to my care team.", ""]

    if card.get("allergies"):
        lines += ["MY ALLERGIES (as I listed them)"]
        lines += [f"  {a}" for a in card["allergies"]]
        lines.append("")

    pet = card.get("pet")
    if pet and pet.get("name"):
        who = f", looked after by {pet['caregiver']}" if pet.get("caregiver") else ""
        lines += ["MY PET", f"  {pet['name']}{who}", ""]

    if card.get("papers"):
        lines += ["WHERE MY IMPORTANT PAPERS ARE", f"  {card['papers']}", ""]

    sections = card.get("sections", ["helpers", "plan"])
    tasks = getattr(state, "helper_tasks", [])
    if tasks and "helpers" in sections:
        lines += ["WHO IS HELPING"]
        for t in tasks:
            when = ", ".join(x for x in (t.get("date", ""), t.get("time", "")) if x)
            task = t["task"] + (f" ({when})" if when else "")
            lines.append(f"  {task}: {t['person'] or 'open, anyone can sign up'}")
        lines.append("")

    items = state.items if "plan" in sections else []
    if items:
        lines += ["MY PLAN"]
    for item in items:
        need = NEEDS[item.need]
        if item.route == "blank":
            lines.append(f"  {need}: not covered yet")
        else:
            ref = directory.by_id.get(item.ref_id or "", {})
            name = ref.get("name", item.label)
            if item.route == "person":
                helpers = [h for n in state.helper_notes for h in n["helpers"]]
                name = " and ".join(helpers) if helpers else "someone you chose"
            phone = ref.get("phone")
            if item.route == "service":
                name += f", {phone}" if phone else ", see website and call to confirm"
            lines.append(f"  {need}: {name}")

    last = reviews.parse(state.reviews[-1]) if state.reviews else None
    lines += [""] + ([f"Plan last reviewed: {reviews.words(last)}"] if last else [])
    lines += [f"Review this plan by: {review_by:%B %Y}", EMERGENCY_LINE]
    return lines


def write_card(card: dict, state: PlanState, directory: Directory, today: date | None = None,
               out_dir: Path = OUTPUT_DIR) -> Path:
    today = today or date.today()
    lines = build_card_lines(card, state, directory, today)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"card-{_slug(card.get('name') or 'me')}"

    base.with_suffix(".txt").write_text("\n".join(lines) + "\n")

    html_path = base.with_suffix(".html")
    html_path.write_text(render_card_html(lines))
    return html_path


SECTION_TITLES = {
    "PEOPLE TO CONTACT": "People to contact",
    "MY MEDICATIONS (as I listed them)": "My medications, in my own words",
    "MY ALLERGIES (as I listed them)": "My allergies, in my own words",
    "MY PET": "My pet",
    "WHERE MY IMPORTANT PAPERS ARE": "Where my important papers are",
    "WHO IS HELPING": "Who is helping",
    "MY PLAN": "My plan",
}

# One black icon per section heading, drawn inline so the card prints without Wi-Fi.
# Each sits next to its heading text, so nothing depends on the picture alone.
SECTION_ICONS = {
    "People to contact": '<path d="M6.6 3h3l1.5 4.5-2 1.5a11 11 0 0 0 5.9 5.9l1.5-2L21 14.4v3A2.6 2.6 0 0 1 18.4 20 15.4 15.4 0 0 1 4 5.6 2.6 2.6 0 0 1 6.6 3z"/>',
    "My medications, in my own words": '<path d="M8.5 3a5.5 5.5 0 0 1 3.9 1.6l7 7a5.5 5.5 0 0 1-7.8 7.8l-7-7A5.5 5.5 0 0 1 8.5 3zm0 2a3.5 3.5 0 0 0-2.5 6l3.1 3.1 5-5L11 6a3.5 3.5 0 0 0-2.5-1z"/>',
    "My allergies, in my own words": '<path d="M12 2 1 21h22zm-1 7h2v6h-2zm0 8h2v2h-2z"/>',
    "My pet": '<circle cx="7" cy="9" r="2.2"/><circle cx="17" cy="9" r="2.2"/><circle cx="10" cy="5.2" r="2"/><circle cx="14" cy="5.2" r="2"/><path d="M12 11c3 0 6 4.4 6 6.6 0 1.8-1.5 2.4-3 2.4-1.2 0-2-.6-3-.6s-1.8.6-3 .6c-1.5 0-3-.6-3-2.4C6 15.4 9 11 12 11z"/>',
    "Where my important papers are": '<path d="M2 5a1 1 0 0 1 1-1h6l2 2h10a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z"/>',
    "Who is helping": '<circle cx="12" cy="7" r="4"/><path d="M4 21a8 8 0 0 1 16 0z"/>',
    "My plan": '<path d="M3 11 12 3l9 8v10h-6v-6H9v6H3z"/>',
    "Still to plan": '<path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zM7 11v2h10v-2z"/>',
    "Places that can help": '<path d="M6.6 3h3l1.5 4.5-2 1.5a11 11 0 0 0 5.9 5.9l1.5-2L21 14.4v3A2.6 2.6 0 0 1 18.4 20 15.4 15.4 0 0 1 4 5.6 2.6 2.6 0 0 1 6.6 3z"/>',
}


def _icon(title: str) -> str:
    paths = SECTION_ICONS.get(title)
    return f'<svg class="sec-icon" viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>' if paths else ""


_FLAG_PREFIX = re.compile(r"^(WAITLIST|NO PHONE LISTED|NOT RECENTLY CHECKED|SAMPLE ENTRY):\s*")
_MONTHS = ("January February March April May June July August September October November December").split()


def render_plan_html(state: PlanState, directory: Directory, today: date | None = None,
                     help_lines: list[dict] | None = None) -> str:
    """"Print my plan": the whole plan on one clean page. Built by code from items that
    passed the tool checks. Card-only details (medications, allergies) are left off."""
    e = html.escape
    today = today or date.today()
    review_by = reviews.review_by(state.reviews, today)
    last = reviews.parse(state.reviews[-1]) if state.reviews else None
    helpers = [h for n in state.helper_notes for h in n["helpers"]]
    rows = []
    for item in state.items:
        ref = directory.by_id.get(item.ref_id or "", {})
        if item.route == "blank":
            what = "Not covered yet"
        elif item.route == "person":
            what = " and ".join(helpers) if helpers else "Someone you chose"
        else:
            what = ref.get("name", item.label)
        extra = []
        if ref.get("phone"):
            extra.append(f"Phone: {ref['phone']}")
        checked = re.match(r"(\d{4})-(\d{2})", ref.get("last_verified", "") if item.route == "service" else "")
        if checked:
            extra.append(f"Info checked {_MONTHS[int(checked[2]) - 1]} {checked[1]}")
        extra += [_FLAG_PREFIX.sub("", f) for f in item.flags]
        small = "".join(f"<li>{e(x)}</li>" for x in extra)
        rows.append(f"<li><strong>{e(NEEDS[item.need])}</strong><br>{e(what)}"
                    f"{f'<ul class=small>{small}</ul>' if small else ''}</li>")
    planned = {i.need for i in state.items}
    left = [NEEDS[n] for n in state.confirmed_needs if n not in planned]
    tasks = ""
    for t in state.helper_tasks:
        when = ", ".join(x for x in (t.get("date", ""), t.get("time", "")) if x)
        task = t["task"] + (f" ({when})" if when else "")
        tasks += f"<li>{e(task)}: {e(t['person'] or 'open, anyone can sign up')}</li>"
    places = "".join(f"<li>{e(l['name'])}: {e(l['how_to_reach'])}</li>" for l in help_lines or [])

    def section(title: str, body: str) -> str:
        return f'<section><h2>{_icon(title)}{e(title)}</h2><ul>{body}</ul></section>' if body else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="color-scheme" content="light only"><meta name="darkreader-lock">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My plan</title>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
 @page {{ size: letter; margin: 0.5in; }}
 body {{ font-family: "Atkinson Hyperlegible", Verdana, Arial, sans-serif; font-size: 15pt; line-height: 1.35;
        color: #000; background: #fff; max-width: 7.5in; margin: 0.3in auto; padding: 0 0.2in; }}
 .banner {{ background: #000; color: #fff; text-align: center; font-weight: 700; font-size: 24pt;
           padding: 0.12in; border-radius: 10px; }}
 .crisis {{ text-align: center; font-weight: 700; margin: 0.1in 0 0.15in; }}
 h1 {{ font-size: 22pt; margin: 0 0 0.08in; }}
 section {{ border: 3px solid #000; border-radius: 10px; padding: 0.06in 0.16in 0.1in; margin-top: 0.12in; }}
 h2 {{ font-size: 15pt; margin: 0.04in 0; display: flex; align-items: center; gap: 0.08in; }}
 .sec-icon {{ width: 18pt; height: 18pt; flex: none; fill: #000; }}
 ul {{ list-style: none; margin: 0; padding: 0; }}
 section > ul > li {{ padding: 0.04in 0; border-top: 1px solid #999; }}
 section > ul > li:first-child {{ border-top: 0; }}
 .small {{ font-size: 12pt; margin-top: 0.02in; }}
 .small li::before {{ content: "- "; }}
 .foot {{ margin-top: 0.12in; font-weight: 700; }}
 .print {{ display: block; margin: 0.25in auto 0; font: inherit; font-weight: 700; padding: 0.1in 0.35in;
          border: 0; border-radius: 10px; background: #1F6F78; color: #fff; cursor: pointer; }}
 @media print {{ .print {{ display: none; }} body {{ margin: 0; }} }}
</style></head>
<body>
 <div class="banner">IN AN EMERGENCY, CALL 911</div>
 <p class="crisis">{e(CRISIS_LINE)}</p>
 <h1>My plan</h1>
 {section("My plan", "".join(rows))}
 {section("Still to plan", "".join(f"<li>{e(x)}</li>" for x in left))}
 {section("Who is helping", tasks)}
 {section("Places that can help", places)}
 {f'<p class="foot">Plan last reviewed: {reviews.words(last)}</p>' if last else ""}
 <p class="foot">Review this plan by: {review_by:%B %Y}</p>
 <p class="foot">{e(EMERGENCY_LINE)}</p>
 <button class="print" onclick="window.print()">Print my plan</button>
</body></html>"""


def render_card_html(lines: list[str]) -> str:
    """Large print, black on white, one page. Built only from the checked lines."""
    title = html.escape(lines[0].split(":", 1)[1].strip()) if ":" in lines[0] else ""
    sections, current = [], None
    for ln in lines[3:]:
        if not ln.strip():
            current = None
            continue
        if ln in SECTION_TITLES:
            current = {"title": SECTION_TITLES[ln], "rows": []}
            sections.append(current)
        elif current is not None and ln.startswith("  "):
            current["rows"].append(ln.strip())
        else:
            sections.append({"title": None, "rows": [ln.strip()]})
            current = None

    parts = []
    for sec in sections:
        rows = "".join(f"<li>{html.escape(r)}</li>" for r in sec["rows"])
        if sec["title"]:
            parts.append(f'<section><h2>{_icon(sec["title"])}{html.escape(sec["title"])}</h2><ul>{rows}</ul></section>')
        else:
            parts.append(f'<p class="foot">{"".join(html.escape(r) for r in sec["rows"])}</p>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="color-scheme" content="light only"><meta name="darkreader-lock">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>If something happens{": " + title if title else ""}</title>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
 @page {{ size: letter; margin: 0.5in; }}
 * {{ box-sizing: border-box; }}
 body {{ font-family: "Atkinson Hyperlegible", Verdana, Arial, sans-serif; font-size: 17pt;
        line-height: 1.35; color: #000; background: #fff; max-width: 7.5in; margin: 0.3in auto; padding: 0 0.2in; }}
 .banner {{ background: #000; color: #fff; text-align: center; font-weight: 700; font-size: 30pt;
           padding: 0.15in; border-radius: 10px; }}
 .crisis {{ text-align: center; font-weight: 700; font-size: 16pt; margin: 0.12in 0 0.2in; }}
 h1 {{ font-size: 24pt; margin: 0 0 0.1in; }}
 section {{ border: 3px solid #000; border-radius: 10px; padding: 0.08in 0.18in 0.12in; margin-top: 0.14in; }}
 h2 {{ font-size: 17pt; margin: 0.04in 0; display: flex; align-items: center; gap: 0.08in; }}
 .sec-icon {{ width: 20pt; height: 20pt; flex: none; fill: #000; }}
 ul {{ list-style: none; margin: 0; padding: 0; }}
 li {{ padding: 0.03in 0; border-top: 1px solid #999; }}
 li:first-child {{ border-top: 0; }}
 .foot {{ margin-top: 0.14in; font-weight: 700; }}
 .print {{ display: block; margin: 0.25in auto 0; font: inherit; font-weight: 700; font-size: 16pt;
          padding: 0.12in 0.4in; border: 0; border-radius: 12px; background: #1F6F78; color: #fff; cursor: pointer; }}
 @media print {{ .print {{ display: none; }} body {{ margin: 0; }} }}
</style></head>
<body>
 <div class="banner">IN AN EMERGENCY, CALL 911</div>
 <p class="crisis">{html.escape(CRISIS_LINE)}</p>
 <h1>If something happens{": " + title if title else ""}</h1>
 {"".join(parts)}
 <button class="print" onclick="window.print()">Print this card</button>
</body></html>"""
