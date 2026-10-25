""""Why this?": one plain sentence on why each part of the plan was chosen.

Built from facts only: the services and preparations lists, and what the person
said (the needs they confirmed, who they said they'd ask). Never written by the
model, so it cannot invent a reason.
"""

from .directory import Directory
from .state import NEEDS, PlanItem, PlanState

COST_WORDS = {"free": "It's free", "low": "It costs little", "paid": "It has a cost"}


# Short words for each need, so the sentence stays easy to read.
SHORT = {"ride_home_after_procedure": "getting home after a procedure",
         "check_in_after_procedure": "a check-in after a procedure",
         "spare_key_access": "a way for help to get in", "pet_care_during_stay": "pet care while you're away",
         "fall_at_home": "a fall at home", "power_outage": "a power outage", "home_repair": "urgent home repairs",
         "transportation": "rides to appointments", "help_at_home": "help at home while you heal",
         "mail_while_away": "your mail while you're away", "family_updates": "keeping family updated"}


def _need_words(need: str) -> str:
    if need in SHORT:
        return SHORT[need]
    label = NEEDS.get(need, need.replace("_", " "))
    return label[:1].lower() + label[1:]


def _prep_cost(rough: str) -> str:
    rough = (rough or "").strip()
    if rough.lower() == "free":
        return "and it's free"
    if rough.lower().startswith("free"):
        return f"and it's {rough.lower()}"
    if not rough or rough.lower() == "varies":
        return "and the cost is up to you"
    return f"and it costs {rough}"


def why_this(item: PlanItem, state: PlanState, directory: Directory) -> str:
    if item.route == "service":
        svc = directory.by_id.get(item.ref_id or "", {})
        cost = COST_WORDS.get(svc.get("cost", ""), "It's on our checked list")
        where = "you can use it from anywhere" if "*" in svc.get("areas", []) else f"it serves {state.town}"
        line = f"{cost}, and {where}. It helps with {_need_words(item.need)}."
        if svc.get("cost") == "paid":
            cheaper = [s for s in directory.find_services(item.need, state.town) if s["cost"] in ("free", "low")]
            if not cheaper:
                line = f"Nothing free on our list helps with {_need_words(item.need)}, so this is the closest match."
        if svc.get("waitlist"):
            line += " It has a waitlist, so it's good to apply early."
        return line
    if item.route == "preparation":
        prep = directory.by_id.get(item.ref_id or "", {})
        return f"It's a simple step you can do yourself, so no one has to be asked, {_prep_cost(prep.get('rough_cost', ''))}."
    if item.route == "person":
        names = [h for n in state.helper_notes for h in n["helpers"]]
        who = " and ".join(names) if names else "someone"
        return f"You said you'd feel comfortable asking {who}."
    return "Nothing on our list fits this yet, so it stays open instead of guessing."
