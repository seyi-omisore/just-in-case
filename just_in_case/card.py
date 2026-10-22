"""Builds the printable "If something happens" refrigerator card.

The card is assembled by code from (a) what the user typed and (b) plan items
that already passed the tool checks. The model cannot add free text to it, so
it cannot slip in medical advice or a service that is not in the directory.
"""

import html
import re
from datetime import date, timedelta
from pathlib import Path

from .config import OUTPUT_DIR, REVIEW_AFTER_MONTHS
from .directory import Directory
from .safeguards import CRISIS_LINE, EMERGENCY_LINE
from .state import NEEDS, PlanState


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "card"


def build_card_lines(card: dict, state: PlanState, directory: Directory, today: date) -> list[str]:
    review_by = today + timedelta(days=30 * REVIEW_AFTER_MONTHS)
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

    pet = card.get("pet")
    if pet and pet.get("name"):
        who = f", looked after by {pet['caregiver']}" if pet.get("caregiver") else ""
        lines += ["MY PET", f"  {pet['name']}{who}", ""]

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

    lines += ["", f"Review this plan by: {review_by:%B %Y}", EMERGENCY_LINE]
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
    "MY PET": "My pet",
    "WHO IS HELPING": "Who is helping",
    "MY PLAN": "My plan",
}


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
            parts.append(f'<section><h2>{html.escape(sec["title"])}</h2><ul>{rows}</ul></section>')
        else:
            parts.append(f'<p class="foot">{"".join(html.escape(r) for r in sec["rows"])}</p>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
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
 h2 {{ font-size: 17pt; margin: 0.04in 0; }}
 ul {{ list-style: none; margin: 0; padding: 0; }}
 li {{ padding: 0.03in 0; border-top: 1px solid #999; }}
 li:first-child {{ border-top: 0; }}
 .foot {{ margin-top: 0.14in; font-weight: 700; }}
 .print {{ display: block; margin: 0.25in auto 0; font: inherit; font-weight: 700; font-size: 16pt;
          padding: 0.12in 0.4in; border: 0; border-radius: 12px; background: #0072B2; color: #fff; cursor: pointer; }}
 @media print {{ .print {{ display: none; }} body {{ margin: 0; }} }}
</style></head>
<body>
 <div class="banner">IN AN EMERGENCY, CALL 911</div>
 <p class="crisis">{html.escape(CRISIS_LINE)}</p>
 <h1>If something happens{": " + title if title else ""}</h1>
 {"".join(parts)}
 <button class="print" onclick="window.print()">Print this card</button>
</body></html>"""
