"""The "next step" card: the one thing to do now.

Built from the plan, never by the model. Only parts the user said "Yes, add this"
to can become a step. "Done" removes a step. "Remind me later" moves it to the end
of the list. Nothing is sent: it comes back when the user opens the page again.
"""

import re

# Most important first. Anything not listed comes last, in plan order.
PRIORITY = [
    "ride_home_after_procedure", "check_in_after_procedure", "spare_key_access",
    "pet_care_during_stay", "power_outage", "fall_at_home", "home_repair",
    "transportation", "help_at_home", "mail_while_away",
]

# Plain words that finish "Call ___ to ___".
ACTION = {
    "ride_home_after_procedure": "book your ride home",
    "check_in_after_procedure": "set up your evening check-in",
    "spare_key_access": "set up a spare key",
    "pet_care_during_stay": "plan care for your pet",
    "power_outage": "get ready for an outage",
    "fall_at_home": "get ready for a fall",
    "home_repair": "line up a repair",
    "transportation": "set up your rides",
    "help_at_home": "ask about help at home",
    "mail_while_away": "hold your mail",
}


def short_name(name: str) -> str:
    """Drop the long bracket part: "Age Strong Commission (Boston's Council on Aging)"."""
    return re.sub(r"\s*\(.*?\)", "", name or "").strip() or "this service"


def usable_phone(phone: str) -> str:
    return phone if phone and len(re.sub(r"\D", "", phone)) >= 7 else ""


def step_text(item: dict) -> str:
    action = ACTION.get(item["need"], "finish this part of your plan")
    name = short_name(item["name"])
    if item["route"] == "person":
        return f"Ask {item['name']} if they can help you {action}."
    if item["route"] == "service":
        if usable_phone(item.get("phone", "")):
            return f"Call {name} to {action}."
        return f"Visit the {name} website to {action}." if item.get("url") else f"Contact {name} to {action}."
    if item.get("url"):
        return f"Use {name} to {action}."
    return f"Set this up: {name}."


def pick_next_step(items: list[dict], done: set, later: list) -> dict | None:
    """The one step to show, or None if the user has not said yes to anything yet."""
    accepted = [i for i in items if i["user_ok"] and i["route"] != "blank"]
    todo = [i for i in accepted if i["need"] not in done]
    if not todo:
        return {"all_done": True} if accepted else None
    rank = lambda i: PRIORITY.index(i["need"]) if i["need"] in PRIORITY else len(PRIORITY)
    todo.sort(key=rank)  # a stable sort keeps plan order for ties
    fresh = [i for i in todo if i["need"] not in later]
    snoozed = sorted((i for i in todo if i["need"] in later), key=lambda i: later.index(i["need"]))
    pick = (fresh + snoozed)[0]
    return {
        "need": pick["need"], "text": step_text(pick),
        "phone": usable_phone(pick.get("phone", "")) if pick["route"] == "service" else "",
        "url": pick.get("url", "") if pick["route"] != "person" else "",
        "left": len(todo) - 1, "snoozed": pick["need"] in later,
    }
