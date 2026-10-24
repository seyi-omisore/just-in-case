"""Warm, plain encouragement at milestones. Fixed wording, never written by the model.

Rules for every line here: calm and respectful, no exclamation points, no childish
words, and nothing that talks down. We name what the person did, then stop.
"""


def encouragement(covered: int, total: int, has_card: bool) -> str | None:
    """One short line about where the plan stands, or None before there is anything to say."""
    if total <= 0 or covered <= 0:
        return None
    if covered >= total:
        if has_card:
            return "Your plan is ready. You can print your card at any time."
        return "Your plan is ready. You can print your plan at any time, and we can make a fridge card when you'd like."
    count = f"You've planned for {covered} of {total} situations."
    return f"{count} A good start." if covered * 2 < total else f"{count} Nice work."
