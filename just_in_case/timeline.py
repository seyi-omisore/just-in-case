"""The "Getting ready" timeline: simple dated steps counting down to the user's day.

The date always comes from the user (a date picker on the page), never from the
model. The steps are fixed wording, written here, for the parts of the plan the
user said yes to. Logistics only: no medical advice. The wording avoids "your"
where it can, so it also reads well when someone is planning for another person.
"""

from datetime import date, timedelta

from .state import PlanState

# need -> [(days before the day, step)]
STEPS: dict[str, list[tuple[int, str]]] = {
    "ride_home_after_procedure": [(14, "Book the ride home, if it isn't booked yet."),
                                  (7, "Confirm the ride home."),
                                  (1, "Check what time to arrive, and when the ride will come.")],
    "check_in_after_procedure": [(1, "Remind the person who will check in that evening.")],
    "spare_key_access": [(3, "Set up the key lockbox."),
                         (1, "Put a key in the lockbox.")],
    "pet_care_during_stay": [(7, "Fill in the pet care sheet and put it on the fridge."),
                             (1, "Remind the pet helper, and leave food where it's easy to find.")],
    "mail_while_away": [(7, "Request USPS Hold Mail. It's free; start a week or more early.")],
    "transportation": [(7, "Confirm the ride.")],
    "help_at_home": [(3, "Confirm meals or help at home for the days after.")],
}
ALWAYS = [(0, "Charge the phone, and keep it close.")]
CARD_STEP = (1, "Put the fridge card where it's easy to see.")

MAX_DAYS_AHEAD = 366

# "The day before" checklist: short, one line per part the user said yes to. Logistics only.
DAY_BEFORE: dict[str, str] = {
    "ride_home_after_procedure": "Confirm the ride home and the pick-up time.",
    "check_in_after_procedure": "Remind the person who will check in that evening.",
    "spare_key_access": "Put a key in the lockbox.",
    "pet_care_during_stay": "Leave pet food where it's easy to find.",
    "mail_while_away": "Check that Hold Mail is set.",
    "transportation": "Confirm the ride and the pick-up time.",
    "help_at_home": "Confirm meals or help for the days after.",
    "power_outage": "Charge flashlights and a phone power bank.",
}
DAY_BEFORE_ALWAYS = "Charge the phone, and keep it close."
DAY_BEFORE_CARD = "Put the fridge card where it's easy to see."


def event_label(state: PlanState) -> str:
    needs = set(state.confirmed_needs)
    if needs & {"ride_home_after_procedure", "check_in_after_procedure"}:
        return "procedure"
    if needs & {"pet_care_during_stay", "mail_while_away"}:
        return "time away"
    return "day"


def when_words(days_before: int) -> str:
    return {0: "The morning of", 1: "The day before", 7: "One week before", 14: "Two weeks before"}.get(
        days_before, f"{days_before} days before")


def countdown(event: date, today: date) -> str:
    days = (event - today).days
    if days < 0:
        return "This date has passed. You can change it."
    if days == 0:
        return "Today is the day."
    if days == 1:
        return "Tomorrow."
    return f"{days} days to go."


def parse_day(value: str, today: date) -> date | None:
    """A date the user picked (YYYY-MM-DD), within the next year. Anything else is ignored."""
    try:
        d = date.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if not (today - timedelta(days=30) <= d <= today + timedelta(days=MAX_DAYS_AHEAD)):
        return None
    return d


def build(state: PlanState, today: date) -> dict:
    """The timeline the page shows. Steps only for parts the user said yes to."""
    planned = [i.need for i in state.items if i.user_ok and i.route != "blank"]
    event = parse_day(state.event_date, today) if state.event_date else None
    out = {"label": event_label(state), "date": event.isoformat() if event else "",
           "countdown": countdown(event, today) if event else "", "steps": []}
    out["day_before"] = None
    if not planned:
        return out
    if event:
        items = [DAY_BEFORE[n] for n in planned if n in DAY_BEFORE]
        if state.card_path:
            items.append(DAY_BEFORE_CARD)
        items.append(DAY_BEFORE_ALWAYS)
        on = event - timedelta(days=1)
        out["day_before"] = {"date": on.isoformat(), "past": on < today, "items": items}
    chosen = [(need, d, text) for need in planned for d, text in STEPS.get(need, [])]
    chosen += [("", d, text) for d, text in ALWAYS]
    if state.card_path:
        chosen.append(("", *CARD_STEP))
    for need, days_before, text in sorted(chosen, key=lambda c: -c[1]):
        sid = f"{need or 'any'}:{days_before}:{text[:12]}"
        on = event - timedelta(days=days_before) if event else None
        out["steps"].append({
            "id": sid, "need": need, "text": text, "when": when_words(days_before),
            "date": on.isoformat() if on else "", "done": sid in state.steps_done,
            "past": bool(on and on < today),
        })
    return out


# ---- The night before: who is doing what, built from the plan --------------

NIGHT_SHORT = {"ride_home_after_procedure": "Getting home", "check_in_after_procedure": "Evening check-in",
               "spare_key_access": "Your key", "pet_care_during_stay": "Your pet", "mail_while_away": "Your mail",
               "transportation": "Your ride", "help_at_home": "Help at home", "power_outage": "Power outage",
               "fall_at_home": "Falls", "home_repair": "Home repairs", "family_updates": "Family updates"}


def night_before(state: PlanState, directory, today: date) -> dict | None:
    """A short, calm summary for the night before a dated event. Only parts the person said yes to,
    and their helper table. Shown on the day before; a preview before that."""
    event = parse_day(state.event_date, today) if state.event_date else None
    planned = [i for i in state.items if i.user_ok and i.route != "blank"]
    if not event or not planned:
        return None
    label = event_label(state)
    title = {"procedure": "Tomorrow is your procedure.", "time away": "Tomorrow you head out."}.get(label, "Tomorrow is the day.")
    lines = []
    names = [h for n in state.helper_notes for h in n["helpers"]]
    for i in planned:
        what = NIGHT_SHORT.get(i.need, i.need.replace("_", " ").capitalize())
        if i.route == "person":
            lines.append(f"{what}: {' and '.join(names) if names else 'the person you asked'}.")
        elif i.ref_id == "prep_key_lockbox":
            lines.append(f"{what}: in the lockbox.")
        else:
            ref = directory.by_id.get(i.ref_id or "", {})
            phone = ref.get("phone", "")
            lines.append(f"{what}: {ref.get('name', i.label)}" + (f", {phone}." if phone else "."))
    for t in state.helper_tasks:
        if t.get("person"):
            when = f", {t['time']}" if t.get("time") else ""
            lines.append(f"{t['person'].split(' ')[0]}: {t['task']}{when}.")
    if state.card_path:
        lines.append("Your fridge card: on the fridge, where it's easy to see.")
    lines.append("In an emergency, call 911.")
    on = event - timedelta(days=1)
    return {"date": on.isoformat(), "show_now": today == on, "title": title,
            "time": state.event_time, "lines": lines}
