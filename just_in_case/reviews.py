"""Plan reviews: a dated record that the person looked over their plan.

The person taps "Mark my plan as reviewed". We keep only the date. A gentle
suggestion to look again comes after a few months (REVIEW_AFTER_MONTHS). It is
never an alarm: no "overdue", no red, no repeated nagging.
"""

from datetime import date, timedelta

from .config import REVIEW_AFTER_MONTHS

MAX_REVIEWS = 20  # the most recent dates we keep
DAYS = 30 * REVIEW_AFTER_MONTHS


def words(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def parse(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def review_by(dates: list[str], today: date) -> date:
    """When a look is next suggested: a few months after the last review, or after today."""
    last = parse(dates[-1]) if dates else None
    return (last or today) + timedelta(days=DAYS)


def review_status(dates: list[str], today: date, has_plan: bool) -> dict | None:
    """What the page shows. None until there is something in the plan to review."""
    if not has_plan:
        return None
    last = parse(dates[-1]) if dates else None
    by = review_by(dates, today)
    if last is None:
        text = "When you are ready, look over your plan and mark it reviewed. Many people do this every few months."
    elif today >= by:
        text = f"It has been a few months since {words(last)}. If you would like, take a few minutes to look it over again."
    else:
        text = f"You last reviewed your plan on {words(last)}. A good time to look again is around {by:%B %Y}."
    return {
        "text": text, "last": last.isoformat() if last else "", "reviewed_today": last == today,
        "history": [words(d) for d in (parse(x) for x in reversed(dates[-5:])) if d],
        "count": len(dates), "next_by": f"{by:%B %Y}",
    }
