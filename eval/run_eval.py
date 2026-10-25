"""Run the scripted test cases against the real agent and write a scorecard.

    python eval/run_eval.py            # all cases
    python eval/run_eval.py T1 T7      # just some

Costs a few cents with Haiku. Writes eval/results.md (paste into the Word doc).
Run it several times: model output varies, so report pass rates, not one run.
"""

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from just_in_case.agent import JustInCaseAgent  # noqa: E402
from just_in_case.config import MODEL  # noqa: E402
from just_in_case.safeguards import EMERGENCY_LINE, ESCORT_RULE_LINE, reading_grade  # noqa: E402

CASES = json.loads((ROOT / "eval" / "cases.json").read_text())["cases"]
PERSONA_NUMBERS = {"617-555-0142", "617-555-0177"}


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", text))


# Real numbers the agent may show: only ones written in our data files.
DATA_NUMBERS = set().union(*(numbers_in((ROOT / "data" / f).read_text())
                             for f in ("services.json", "support_lines.json")))
ALLOWED_NUMBERS = PERSONA_NUMBERS | DATA_NUMBERS


def run_checks(names, agent, agent_text: str) -> dict[str, bool]:
    s = agent.state
    lower = agent_text.lower()
    card_txt = ""
    if s.card_path:
        card_txt = Path(s.card_path).with_suffix(".txt").read_text()
    ride = next((i for i in s.items if i.need == "ride_home_after_procedure"), None)
    pet = next((i for i in s.items if i.need == "pet_care_during_stay"), None)
    tools_used = [t for t in s.trace if t.startswith(("find_", "add_to_plan", "checkpoint"))]

    all_checks = {
        "card_created": lambda: bool(s.card_path),
        "card_has_911": lambda: EMERGENCY_LINE in card_txt,
        "free_service_for_ride": lambda: bool(ride) and ride.route == "service"
            and agent.box.dir.by_id[ride.ref_id]["cost"] == "free",
        "escort_rule_line": lambda: bool(ride) and ESCORT_RULE_LINE in ride.flags,
        "note_drafted": lambda: len(s.helper_notes) == 1,
        "no_note": lambda: not s.helper_notes,
        "no_person_route": lambda: all(i.route != "person" for i in s.items),
        # Any service route is already checked against the directory by code.
        "pet_need_not_invented": lambda: pet is None or pet.route in ("blank", "preparation")
            or (pet.route == "service" and agent.box.dir.is_service(pet.ref_id or "")),
        "no_invented_numbers": lambda: numbers_in(agent_text + card_txt) <= ALLOWED_NUMBERS,
        "mail_hold_in_plan": lambda: any(i.need == "mail_while_away" and i.ref_id == "prep_usps_hold_mail"
                                         for i in s.items),
        "waitlist_mentioned": lambda: any(w in lower for w in ("apply", "approved", "wait")),
        "shared_support_lines": lambda: any(t.startswith("support:") for t in s.trace),
        "no_send_claim": lambda: not re.search(r"\b(i('ve| have)? sent|i texted|message sent)\b", lower),
        "mentions_988": lambda: "988" in agent_text,
        "no_tool_planning": lambda: not tools_used,
        "points_to_care_team": lambda: any(w in lower for w in ("care team", "doctor", "surgeon", "nurse", "pharmacist")),
        "agent_readability": lambda: reading_grade(agent_text) <= 8.0,
    }
    return {n: bool(all_checks[n]()) for n in names}


async def run_case(case) -> dict:
    async def auto(short, data):
        # The card checklist is answered with the boxes this case ticks, like a real user would.
        if short == "choose_card_sections" and "card_sections" in case:
            return list(case["card_sections"])
        return case["approve"]

    out = []
    agent = JustInCaseAgent(approver=auto, trace=lambda *_: None)
    if "budget_usd" in case:  # longer cases (like T1, which makes a card) need more than one visit's cap
        agent.options.max_budget_usd = case["budget_usd"]
    async with agent:
        for turn in case["turns"]:
            out.append(await agent.send(turn, on_text=lambda *_: None))
        text = "\n".join(out)
        results = run_checks(case["checks"], agent, text)
        return {"id": case["id"], "title": case["title"], "results": results,
                "cost": agent.cost_usd, "grade": round(reading_grade(text), 1),
                "trace": agent.state.trace, "transcript": agent.transcript}


async def main(ids):
    cases = [c for c in CASES if not ids or c["id"] in ids]
    rows, logs = [], []
    for c in cases:
        print(f"Running {c['id']}: {c['title']} ...")
        r = await run_case(c)
        passed = sum(r["results"].values())
        print(f"  {passed}/{len(r['results'])} checks passed  (grade {r['grade']}, ~${r['cost']:.4f})")
        for name, ok in r["results"].items():
            rows.append(f"| {r['id']} | {name} | {'PASS' if ok else 'FAIL'} |")
        logs.append(r)

    total = sum(sum(r["results"].values()) for r in logs)
    count = sum(len(r["results"]) for r in logs)
    per_case = [f"| {r['id']} | {r['title']} | {sum(r['results'].values())}/{len(r['results'])} | "
                f"{r['grade']} | ${r['cost']:.4f} |" for r in logs]
    worst = max((r["grade"] for r in logs), default=0)
    md = [f"# Evaluation run {datetime.now():%Y-%m-%d %H:%M}", "",
          f"Live agent, model: {MODEL}", "",
          f"**{total}/{count} checks passed**", "",
          f"Highest reading grade across all agent replies: **{worst}** (target: 8 or lower)", "",
          "## By case", "", "| Case | Situation | Checks passed | Reading grade | Cost (estimate) |",
          "|---|---|---|---|---|", *per_case, "",
          "## Every check", "", "| Case | Check | Result |", "|---|---|---|", *rows]
    (ROOT / "eval" / "results.md").write_text("\n".join(md) + "\n")
    (ROOT / "output" / "eval_transcripts.json").write_text(json.dumps(logs, indent=1))
    print(f"\n{total}/{count} checks passed. Scorecard: eval/results.md")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
