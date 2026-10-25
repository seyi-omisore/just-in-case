"""Invitations and reminders the person sends to a helper, from their own phone or email.

The agent writes the message from fixed wording here (never the model), so every message
passes the same checks as a helper note: no pressure words, an easy way to say no,
and grade 8 or lower. Nothing here sends anything. The page only fills in a text or
email draft, and the person decides whether to send it.

The helper's phone number or email never comes here: it stays in the person's browser.
"""

import re
from datetime import date

from .config import AGENT_NAME
from .safeguards import check_helper_note

INVITE_SUBJECT = "Could you help with something small?"
REMINDER_SUBJECT = "A friendly reminder"
# Added at the end of an invite when the person has shared their helper list.
LINK_LINE = "If you'd like to help, you can add your name here:"
# Said wherever the person sends a message, so it is always clear who sends it.
YOU_SEND_LINE = f"{AGENT_NAME} writes the message. You send it from your own phone or email."
# A reminder button shows up this many days before a task's date (and on the day).
REMIND_DAYS_BEFORE = 3

# "My people": one invitation the person can keep and reuse for each person they add.
# It is fixed wording with a slot for the first name, which the page fills in. The server
# never sees the person's people: it only hands out this blank template.
NAME_SLOT = "{name}"
PEOPLE_TEMPLATE = (f"Hi {NAME_SLOT},\n\n"
                   "I'm making a small backup plan, just in case. I made a short list of things I'd like "
                   "help with, so you can pick what fits your schedule.\n\n"
                   "No pressure at all. It's completely fine if you can't, and there's no need to reply.")


def people_invite_text(name: str) -> str:
    first = name.strip().split(" ")[0] if name.strip() else ""
    return PEOPLE_TEMPLATE.replace(NAME_SLOT, first) if first else PEOPLE_TEMPLATE.replace(f" {NAME_SLOT}", "")


def _when(row: dict) -> str:
    parts = [p for p in (row.get("date", "").strip(), row.get("time", "").strip()) if p]
    return (", " + ", ".join(parts)) if parts else ""


def _hello(row: dict) -> str:
    name = row.get("person", "").strip().split(" ")[0] if row.get("person", "").strip() else ""
    return f"Hi {name}," if name else "Hi,"


def _task(row: dict) -> str:
    task = row.get("task", "").strip()
    return task[:1].upper() + task[1:]


def invite_text(row: dict) -> str:
    task = _task(row)
    return (f"{_hello(row)}\n\n"
            f"I'm making a small backup plan, just in case. Could you help with this?\n\n"
            f"{task}{_when(row)}\n\n"
            f"No pressure at all. It's completely fine if you can't, and there's no need to reply.")


def reminder_text(row: dict) -> str:
    task = _task(row)
    return (f"{_hello(row)}\n\n"
            f"A friendly reminder about this:\n\n"
            f"{task}{_when(row)}\n\n"
            f"Thank you so much for helping. If your plans have changed, that's completely fine. "
            f"Just let me know.")


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december")


def read_task_date(text: str, today: date) -> date | None:
    """A helper-table date, only when we can read it for sure: "2026-11-14", "11/14", "Nov 14",
    "November 14, 2026". No year means the next time that date comes around. Same rules as the page."""
    s = str(text or "").strip().lower()
    y = m = d = None
    if hit := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        y, m, d = int(hit[1]), int(hit[2]), int(hit[3])
    elif hit := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s):
        m, d, y = int(hit[1]), int(hit[2]), int(hit[3]) if hit[3] else None
    elif hit := re.fullmatch(r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", s):
        word = "sep" if hit[1] == "sept" else hit[1]
        found = [i for i, name in enumerate(MONTHS, 1) if name.startswith(word)]
        if not found or not (word == MONTHS[found[0] - 1] or len(word) == 3):
            return None
        m, d, y = found[0], int(hit[2]), int(hit[3]) if hit[3] else None
    else:
        return None
    if y is not None and y < 100:
        y += 2000
    try:
        if y is None:
            y = today.year
            if date(y, m, d) < today:
                y += 1
        return date(y, m, d)
    except ValueError:   # like "2/31"
        return None


def remind_now(row: dict, today: date) -> bool:
    """Show "Send a reminder" from a few days before the task's date through the day itself,
    and only when someone has been invited or is helping."""
    when = read_task_date(row.get("date", ""), today)
    if when is None or not (row.get("person") or row.get("invited")):
        return False
    return 0 <= (when - today).days <= REMIND_DAYS_BEFORE


def update_text(name: str, event_words: str, when_words: str) -> str:
    """ "My plans changed": one short update for a helper. Same rules as every note."""
    first = name.strip().split(" ")[0] if name.strip() else ""
    return (f"{'Hi ' + first + ',' if first else 'Hi,'}\n\n"
            f"A quick update: my {event_words} has moved. It's now {when_words}.\n\n"
            f"If that changes things for you, that's completely fine. Just let me know.")


def drafts_for(tasks: list[dict], today: date | None = None) -> dict[int, dict]:
    """The invite and reminder drafts for each task, keyed by task id. Each one is checked."""
    today = today or date.today()
    out = {}
    for t in tasks:
        invite, reminder = invite_text(t), reminder_text(t)
        out[t["id"]] = {"invite": invite, "reminder": reminder,
                        "invite_ok": check_helper_note(invite) is None,
                        "reminder_ok": check_helper_note(reminder) is None,
                        "remind_now": remind_now(t, today)}
    return out
