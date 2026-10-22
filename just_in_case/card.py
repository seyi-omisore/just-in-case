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
        f"IF SOMETHING HAPPENS: {card['name']}",
        EMERGENCY_LINE.upper(),
        CRISIS_LINE,
        "",
        "PEOPLE TO CONTACT",
    ]
    for c in card.get("contacts", []):
        lines.append(f"  {c['name']} ({c['relationship']}): {c['phone']}")
    if not card.get("contacts"):
        lines.append("  (none listed)")

    lines += ["", "MY MEDICATIONS (as I listed them)"]
    for m in card.get("medications", []):
        lines.append(f"  {m}")
    if not card.get("medications"):
        lines.append("  (none listed)")
    lines.append("  Questions about medications go to my care team.")

    pet = card.get("pet")
    if pet and pet.get("name"):
        lines += ["", "MY PET", f"  {pet['name']}, looked after by {pet.get('caregiver') or '(not set)'}"]

    lines += ["", "MY PLAN"]
    for item in state.items:
        need = NEEDS[item.need]
        if item.route == "blank":
            lines.append(f"  {need}: not covered yet")
        else:
            ref = directory.by_id.get(item.ref_id or "", {})
            name = ref.get("name", item.label)
            phone = ref.get("phone")
            if item.route == "service":
                name += f", {phone}" if phone else ", see website and call to confirm"
            lines.append(f"  {need}: {name}")
    if not state.items:
        lines.append("  (no plan items yet)")

    lines += ["", f"Review this plan by: {review_by:%B %Y}", EMERGENCY_LINE]
    return lines


def write_card(card: dict, state: PlanState, directory: Directory, today: date | None = None,
               out_dir: Path = OUTPUT_DIR) -> Path:
    today = today or date.today()
    lines = build_card_lines(card, state, directory, today)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"card-{_slug(card['name'])}"

    base.with_suffix(".txt").write_text("\n".join(lines) + "\n")

    # Large print, high contrast, one page. Print from the browser.
    body = []
    for ln in lines:
        if not ln:
            continue
        esc = html.escape(ln.strip())
        if ln.isupper() or ln == lines[0]:
            body.append(f"<h2>{esc}</h2>")
        else:
            body.append(f"<p>{esc}</p>")
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>If something happens</title>
<style>
 body {{ font-family: Arial, sans-serif; font-size: 18pt; color:#000; background:#fff; max-width: 7.5in; margin: .4in auto; }}
 h2 {{ font-size: 20pt; margin: .35em 0 .15em; border-bottom: 3px solid #000; }}
 h2:nth-of-type(2) {{ background:#000; color:#fff; padding:.2em .3em; border:0; }}
 p {{ margin: .1em 0 .1em .3em; }}
</style></head><body>
{''.join(body)}
</body></html>"""
    html_path = base.with_suffix(".html")
    html_path.write_text(page)
    return html_path
