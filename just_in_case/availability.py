"""When a helper is free, and whether that fits a task.

A helper on the shared page picks days (weekdays, weekends, or any day) and times (mornings,
afternoons, or any time). These plain rules, never the model, decide whether that fits the
task's date and time. A date or time we can't read for sure never counts against anyone.
"""

import re
from datetime import date

from .invites import read_task_date

DAYS = {"any": "Any day", "weekdays": "Weekdays", "weekends": "Weekends"}
TIMES = {"any": "Any time", "mornings": "Mornings", "afternoons": "Afternoons"}


def clean(days, times) -> tuple[str, str]:
    return (days if days in DAYS else "any", times if times in TIMES else "any")


def part_of_day(text: str) -> str | None:
    """ "3 pm" -> "afternoons", "9:30 AM" -> "mornings", "evening" -> "evenings". None if unsure."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    if re.search(r"\bmorning", s):
        return "mornings"
    if re.search(r"\b(afternoon|noon|lunch)", s):
        return "afternoons"
    if re.search(r"\b(evening|night|dinner|supper)", s):
        return "evenings"
    hit = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b\.?", s) or re.search(r"\b(\d{1,2}):(\d{2})\b()", s)
    if not hit:
        return None
    hour, ampm = int(hit[1]), hit[3]
    if hour > 23 or (ampm and not 1 <= hour <= 12):
        return None
    if ampm == "p" and hour < 12:
        hour += 12
    elif ampm == "a" and hour == 12:
        hour = 0
    elif not ampm and hour < 12:
        return None   # "3:00" alone could be morning or afternoon: we don't guess
    return "mornings" if hour < 12 else "afternoons" if hour < 17 else "evenings"


def fits(task: dict, days: str, times: str, today: date) -> bool:
    days, times = clean(days, times)
    when = read_task_date(task.get("date", ""), today)
    if when and days != "any" and (days == "weekdays") == (when.weekday() >= 5):
        return False
    part = part_of_day(task.get("time", ""))
    return not (part and times != "any" and part != times)


DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def mismatch(task: dict, days: str, times: str, today: date) -> str | None:
    """Why an offer does not fit, in plain words about the task: "at 3 pm", "on a Saturday". None if it fits."""
    days, times = clean(days, times)
    when = read_task_date(task.get("date", ""), today)
    if when and days != "any" and (days == "weekdays") == (when.weekday() >= 5):
        return f"on a {DAY_NAMES[when.weekday()]}"
    part = part_of_day(task.get("time", ""))
    if part and times != "any" and part != times:
        t = task.get("time", "").strip()
        return f"at {t}" if t[:1].isdigit() else f"in the {part.rstrip('s')}"
    return None


def free_phrase(days: str, times: str) -> str:
    """For "Doris is free ___": "mornings", "on weekdays", "on weekday afternoons", "any time"."""
    words = free_words(days, times)
    return words if words in ("any time", "mornings", "afternoons") else "on " + words


def free_words(days: str, times: str) -> str:
    """ "weekday afternoons", "mornings", "weekends", or "any time"."""
    days, times = clean(days, times)
    if days == "any":
        return "any time" if times == "any" else times
    if times == "any":
        return days
    return {"weekdays": "weekday", "weekends": "weekend"}[days] + " " + times
