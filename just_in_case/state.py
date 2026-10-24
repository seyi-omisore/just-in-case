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

# "Why this matters" tips on the page. Fixed wording, never from the model.
# Plain and general: no medical advice. Each one is tested for grade 8 or lower.
NEED_WHY: dict[str, str] = {
    "ride_home_after_procedure": "Many clinics will not let you go home alone after sedation. "
                                 "Without a ride set, they may move your date.",
    "check_in_after_procedure": "You may feel sleepy or slow for the rest of the day. "
                                "A short check-in that evening is a comfort.",
    "spare_key_access": "If you can't get to the door, help can still get in. "
                        "No one has to break a door or a window.",
    "pet_care_during_stay": "Your pet is fed and safe, even if a hospital stay lasts longer than planned.",
    "fall_at_home": "Many falls happen at home. A few small steps make help easier to reach.",
    "power_outage": "A home can get cold fast in winter without power. "
                    "A plan made now is easier than one made in the dark.",
    "home_repair": "A broken furnace or a leak is less stressful when you already know who to call.",
    "transportation": "Steady rides mean you don't miss checkups when driving is hard.",
    "help_at_home": "Meals and chores can be hard while you heal. Help lets you rest.",
    "mail_while_away": "A full mailbox shows that no one is home. Holding your mail keeps it safe.",
}

ROUTES = ("service", "preparation", "person", "blank")

# What can go on the fridge card. The user ticks these; nothing is assumed.
CARD_SECTIONS: dict[str, str] = {
    "name": "My name at the top",
    "contacts": "People to contact",
    "medications": "My medications",
    "allergies": "My allergies",
    "pet": "My pet and who looks after it",
    "papers": "Where my important papers are",
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
    user_ok: bool = False  # the user tapped "Yes, add this". Progress counts only these.


@dataclass
class PlanState:
    town: str = "Boston"
    confirmed_needs: list[str] = field(default_factory=list)
    comfortable_asking_people: bool = False  # safe default: no people until she says yes
    worried_about_burden: bool = False
    no_one_to_ask: bool = False  # services-only plan
    # Planning for someone else: nothing is planned until that person agrees.
    helping_someone: bool = False
    person_agreed: bool = False
    planning_for: str = ""
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
    # "Getting ready" timeline: the date the user picked, and the steps they ticked.
    event_date: str = ""
    steps_done: set = field(default_factory=set)
    # "Your next step" card: parts the user marked Done, and parts they moved to later.
    actions_done: set = field(default_factory=set)
    actions_later: list = field(default_factory=list)
    # Dates (YYYY-MM-DD) the user marked their plan as reviewed, oldest first.
    reviews: list = field(default_factory=list)
    # The day the user first confirmed what to plan for (YYYY-MM-DD). Shown on the readiness summary.
    started: str = ""

    def people_allowed(self) -> bool:
        return self.comfortable_asking_people and not self.worried_about_burden

    def services_only(self) -> bool:
        """No one to ask, or would rather not ask: the plan is services and preparations."""
        return (self.no_one_to_ask or self.worried_about_burden) and not self.people_allowed()

    def waiting_for_agreement(self) -> bool:
        return self.helping_someone and not self.person_agreed

    def helper_count(self) -> int:
        return sum(len(n["helpers"]) for n in self.helper_notes)

    def heard(self) -> str:
        return "\n".join(self.user_said)

    def log(self, line: str) -> None:
        self.trace.append(line)
