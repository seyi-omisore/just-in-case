"""The plan being built in one session.

The model never edits this directly. It changes only when a tool runs, and each
tool checks the rules first. Nothing is saved to a database; it lives in memory
for one session (privacy: store nothing).
"""

from dataclasses import dataclass, field

# Every need the agent can work on. Tools reject anything else, so the model
# cannot invent new categories mid-conversation.
NEEDS: dict[str, str] = {
    "ride_home_after_procedure": "Getting home safely after a procedure with sedation",
    "check_in_after_procedure": "Someone checking in the evening after a procedure",
    "spare_key_access": "A way for help to get in if you cannot reach the door",
    "pet_care_during_stay": "Care for a pet during a hospital stay",
    "fall_at_home": "Being prepared for a fall at home",
    "power_outage": "Staying safe in a winter power outage",
    "home_repair": "Urgent home problems, like a broken furnace",
    "transportation": "Rides to regular appointments",
    "help_at_home": "Help with meals or chores at home while recovering",
    "mail_while_away": "Keeping your mail safe while you are away or in the hospital",
}

ROUTES = ("service", "preparation", "person", "blank")

# What can go on the fridge card. The user ticks these; nothing is assumed.
CARD_SECTIONS: dict[str, str] = {
    "contacts": "People to contact",
    "medications": "My medications",
    "pet": "My pet and who looks after it",
    "helpers": "Who's helping",
    "plan": "My plan",
}


@dataclass
class PlanItem:
    need: str
    route: str  # one of ROUTES
    ref_id: str | None  # service or preparation id, if any
    label: str  # what shows on the plan
    flags: list[str] = field(default_factory=list)


@dataclass
class PlanState:
    town: str = "Boston"
    confirmed_needs: list[str] = field(default_factory=list)
    comfortable_asking_people: bool = False  # safe default: no people until she says yes
    worried_about_burden: bool = False
    items: list[PlanItem] = field(default_factory=list)
    helper_notes: list[dict] = field(default_factory=list)
    card_path: str | None = None
    trace: list[str] = field(default_factory=list)  # decisions, for the demo and evals
    # Everything the user typed this visit. The card may only use details found here.
    user_said: list[str] = field(default_factory=list)
    # The helper table: [{"id", "task", "date", "time", "person"}]
    helper_tasks: list[dict] = field(default_factory=list)
    # Card sections the user picked. None means we have not asked yet.
    card_sections: list[str] | None = None
    next_task_id: int = 1

    def people_allowed(self) -> bool:
        return self.comfortable_asking_people and not self.worried_about_burden

    def helper_count(self) -> int:
        return sum(len(n["helpers"]) for n in self.helper_notes)

    def heard(self) -> str:
        return "\n".join(self.user_said)

    def log(self, line: str) -> None:
        self.trace.append(line)
