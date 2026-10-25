"""What's new: the helpers' replies, gathered and shown to the person.

Built by code each time the plan is shown, from the offers on the shared helper list and
the task's own date and time. Never by the model. Each item has clear buttons; nothing
here sends anything or changes the plan by itself.
"""

from datetime import date

from .availability import fits, free_words, mismatch, free_phrase
from .directory import Directory
from .invites import REMIND_DAYS_BEFORE, read_task_date
from .state import PlanState

# Words in a task that point to a need with services, for a backup when no one has offered.
TASK_NEEDS = (
    (("drive", "ride", "pick me up", "take me home", "get home"), "ride_home_after_procedure"),
    (("feed", "pet", "cat", "dog", "walk the"), "pet_care_during_stay"),
    (("check in", "check on"), "check_in_after_procedure"),
    (("appointment",), "transportation"),
)


def _first(name: str) -> str:
    return name.strip().split(" ")[0] if name.strip() else ""


def _when(t: dict) -> str:
    parts = [p for p in (t.get("date", "").strip(), t.get("time", "").strip()) if p]
    return (", " + ", ".join(parts)) if parts else ""


def _task(t: dict) -> str:
    task = t["task"].strip().rstrip(".")
    return task[:1].lower() + task[1:]


def works_for(o: dict) -> str:
    words = free_words(o.get("days"), o.get("times"))
    return f"{words[:1].upper() + words[1:]} {'works' if words == 'any time' else 'work'} for {_first(o['name'])}."


def backup_service(t: dict, state: PlanState, directory: Directory) -> dict | None:
    text = t["task"].lower()
    for words, need in TASK_NEEDS:
        if any(w in text for w in words):
            found = directory.find_services(need, state.town)
            if found:
                s = found[0]
                return {"name": s["name"], "phone": s["phone"], "url": s.get("url", "")}
    return None


def _soon(t: dict, today: date) -> int | None:
    when = read_task_date(t.get("date", ""), today)
    if when is None:
        return None
    days = (when - today).days
    return days if 0 <= days <= REMIND_DAYS_BEFORE else None


def _days_words(n: int) -> str:
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def whats_new(state: PlanState, directory: Directory, today: date, shared: bool) -> list[dict]:
    out: list[dict] = []
    for t in state.helper_tasks:
        if t.get("person") and t.get("confirmed"):
            continue
        offers = t.get("offers") or []
        person = t.get("person", "")
        base = {"task_id": t["id"]}
        chooser = len(offers) >= 2 and not t.get("chosen")
        if chooser:
            names = [_first(o["name"]) for o in offers]
            out.append({**base, "kind": "choose",
                        "text": f"{', '.join(names[:-1])} and {names[-1]} offered to help with: {_task(t)}. Who would you like?",
                        "choices": [{"offer_id": o["id"], "name": _first(o["name"]), "works": works_for(o),
                                     "note": o.get("note", ""), "fits": fits(t, o["days"], o["times"], today)}
                                    for o in offers]})
            continue
        if person and offers:
            o = next((x for x in offers if x["name"].lower() == person.lower()), None)
            if o:
                out.append({**base, "kind": "helping", "note": o.get("note", ""), "name": _first(o["name"]),
                            "text": f"{_first(o['name'])} can help with: {_task(t)}{_when(t)}. {works_for(o)}"})
            continue
        if not person and len(offers) == 1:
            o = offers[0]
            why = mismatch(t, o["days"], o["times"], today)
            if why:
                out.append({**base, "kind": "mismatch", "note": o.get("note", ""), "name": _first(o["name"]),
                            "text": f"{_first(o['name'])} offered to help with: {_task(t)}. {_first(o['name'])} is free "
                                    f"{free_phrase(o['days'], o['times'])}, but this task is {why}. "
                                    "Would you like to ask someone else, or change the time?"})
            else:   # it fits now (for example, the person changed the time): one tap to choose them
                out.append({**base, "kind": "fits_now", "note": o.get("note", ""), "offer_id": o["id"], "name": _first(o["name"]),
                            "text": f"{_first(o['name'])} can help with: {_task(t)}{_when(t)}. {works_for(o)}"})
            continue
        if not person and not offers and (shared or t.get("invited")):
            n = _soon(t, today)
            if n is None:
                out.append({**base, "kind": "none", "text": f"No one has offered to help with: {_task(t)} yet."})
            else:
                out.append({**base, "kind": "soon", "task": t["task"].strip(),
                            "text": f"No one has offered yet, and this is {_days_words(n)}: {_task(t)}.",
                            "backup": backup_service(t, state, directory)})
    return out
