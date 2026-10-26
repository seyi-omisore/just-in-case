"""What needs attention: the agent checks the plan each time it is shown.

Only real gaps, worked out by code from the plan and the services list (never by the
model): a part that is not covered, a helper who has not confirmed, a service with a
waitlist, or service details more than six months old. Each gap has one clear button.
Nothing here sends anything or changes the plan; the buttons only open the right place.
"""

from datetime import date

from .directory import Directory
from .state import NEEDS, PlanState

OLD_AFTER_DAYS = 183   # about six months
ALL_SET = "You're all set. Nothing needs your attention right now."


def _need(need: str) -> str:
    return NEEDS.get(need, need.replace("_", " "))


def check(state: PlanState, directory: Directory, today: date) -> list[dict]:
    out: list[dict] = []
    planned = {i.need: i for i in state.items}
    for need in state.confirmed_needs:
        item = planned.get(need)
        if item is None or item.route == "blank":
            out.append({"kind": "uncovered", "need": need, "text": f"Not covered yet: {_need(need)}.",
                        "action": "See where to look"})
    for i in state.items:
        if i.route != "service":
            continue
        svc = directory.by_id.get(i.ref_id or "", {})
        if svc.get("waitlist"):
            out.append({"kind": "waitlist", "need": i.need, "text": f"{svc['name']} has a waitlist. It's good to apply early.",
                        "action": "Call to apply", "phone": svc.get("phone", "")})
        verified = svc.get("last_verified")
        if verified and (today - date.fromisoformat(verified)).days > OLD_AFTER_DAYS:
            out.append({"kind": "old_info", "need": i.need,
                        "text": f"{svc['name']}'s details were last checked over six months ago.",
                        "action": "Call to confirm", "phone": svc.get("phone", "")})
    for t in state.helper_tasks:
        if t.get("confirmed") and t.get("person"):
            continue
        if t.get("offers") or t.get("invited"):
            continue   # a reply on the shared list, or a text the person sent: it is in What's new instead
        if t.get("person") or t.get("invited"):
            who = t["person"].split(" ")[0] if t.get("person") else "Your helper"
            out.append({"kind": "unconfirmed", "task_id": t["id"],
                        "text": f"{who} hasn't confirmed yet: {t['task']}.", "action": "Send a reminder"})
    return out
