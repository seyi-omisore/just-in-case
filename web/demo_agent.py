"""The planner that works without AI. No AI, no internet, no cost.

Used for:
  - "Make my plan" when no AI key is set up: the guided planner. It asks one
    question at a time and builds the plan from the app's own rules and the
    services list, using only what the visitor typed or tapped.
  - "See Ruth's example": the same planner, while the page types Ruth's
    made-up answers (the page does that, never this file).
  - testing the website without spending API money (python serve.py --demo)

It calls the REAL tools and the REAL approval gate, so every rule and
checkpoint behaves exactly as it does with the live agent: the visitor says yes
to each step, only listed services appear, and free options come first.
"""

import asyncio
import re

from just_in_case.agent import make_gate
from just_in_case.directory import Directory
from just_in_case.safeguards import NO_ONE_LINE, separate_service_line
from just_in_case.state import PlanState
from just_in_case.tools import PREFIX, Toolbox, ToolError

PAUSE = 0.5  # seconds between steps, so the activity feed is readable

CRISIS = re.compile(r"notice|who would (even )?know|no one would|nobody would|point anymore", re.I)
BURDEN = re.compile(r"bother|impose|burden|rather not|don'?t want to ask", re.I)
HELPING = re.compile(r"helping (someone|my|a )|for (my )?(mom|mother|dad|father|parent|aunt|uncle|"
                     r"grand\w+|friend|neighbou?r|sister|brother)|someone else", re.I)
NO_ONE = re.compile(r"no one|nobody|don'?t have anyone|do not have anyone|not anyone|no family|no friends", re.I)
NO = re.compile(r"^\s*(no|nope|none|not really|nothing)\b", re.I)
# Keeping family and friends updated during a health event, or many people helping over time.
UPDATES = re.compile(r"keep(ing)? (my |our |the )?(family|friends|kids|children|everyone|people)\b[^.?!]*"
                     r"\b(updated|posted|informed|in the loop|up to date)|updates? (to|for|with) (my |our )?(family|friends)|"
                     r"\b(lots of|many|a lot of) (people|friends|neighbou?rs)|sign up to help|meal (train|schedule)|"
                     r"take turns", re.I)

# Keywords -> needs. The planner only plans for what the visitor brings up.
NEED_WORDS = [
    ("ride_home_after_procedure", r"procedure|surgery|sedation|operation|take me home|ride home"),
    ("pet_care_during_stay", r"\bpets?\b|\bcats?\b|\bdogs?\b|\bbird\b|kitten|puppy"),
    ("mail_while_away", r"hospital|away|stay|nights?\b|trip|travel|leav(e|ing)\b|vacation|holiday|out of town|"
                        r"\bgone\b|weeks?\b|flight|cruise|visit(ing)?\b"),
    ("power_outage", r"power|outage|storm|electric"),
]
NOT_NAMES = {"I", "I'm", "Im", "Yes", "No", "Okay", "Ok", "Sure", "Hi", "Hello", "Thanks", "Thank",
             "Maybe", "My", "She", "He", "They", "The", "And", "But", "Or", "Probably", "Well",
             "Boston", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
PHONE = r"\(?\d{3}\)?[-. ]?\d{3}[-. ]\d{4}"


def names_in(text: str) -> list[str]:
    found = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\b", text)
    out = []
    for f in found:
        first = f.split()[0]
        if first not in NOT_NAMES and f not in out:
            out.append(f)
    return out[:3]


# A short name for each part, used in "Here's what I found".
LABELS = {"ride_home_after_procedure": "Getting home", "spare_key_access": "Spare key",
          "mail_while_away": "Mail", "power_outage": "Power", "pet_care_during_stay": "Your pet"}
TOPICS = ("a ride home after a procedure, going away (your mail), care for a pet, "
          "or a winter power outage")


class DemoAgent:
    """The planner without AI (see the top of this file)."""

    def __init__(self, approver, trace=print, on_tool=None, directory: Directory | None = None,
                 out_dir=None, mode: str = "guided"):
        self.mode = mode   # "guided" for a visitor's own plan, "example" for Ruth's
        self.state = PlanState(town="Boston")
        self.box = Toolbox(self.state, directory or Directory(),
                           **({"out_dir": out_dir} if out_dir else {}))
        self.trace = trace
        self.on_tool = on_tool or (lambda *_: None)
        self.gate = make_gate(approver, trace, precheck=self.box.precheck)
        self.turn = 0
        self.task = ""
        self.contacts: list[dict] = []
        self.meds: list[str] = []
        self.allergies: list[str] = []
        self.pet = ""
        self.papers = ""
        self.name = ""
        self.queue: list[str] = []
        self.suggestion: dict | None = None   # an optional outside service, offered once, never added for them
        self.suggested = False
        self.cost_usd = 0.0
        self.transcript: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    # ---- helpers ------------------------------------------------------------
    async def _call(self, short: str, fn, *args):
        """Run one tool the same way the SDK would: announce, gate, run."""
        data = {"args": list(args)}
        if short == "confirm_needs":
            data = {"needs": args[0]}
        elif short == "draft_helper_note":
            data = {"helper_names": args[0], "role": args[1], "note_text": args[2]}
        elif short == "make_fridge_card":
            data = {"name": args[0], "contacts": args[1], "medications": args[2],
                    "pet_name": args[3], "pet_caregiver": args[4], "allergies": args[5], "papers": args[6]}
        elif short in ("find_services", "find_preparations"):
            data = {"need": args[0]}
        elif short == "add_to_plan":
            data = {"need": args[0], "route": args[1]}
        elif short == "choose_card_sections":
            data = {"sections": []}
        elif short == "add_helper_task":
            data = {"task": args[0], "person": args[1], "date": args[2], "time": args[3]}
        elif short == "confirm_helping_someone":
            data = {"person_name": args[0]}
        self.on_tool(short, data)
        await asyncio.sleep(PAUSE)
        decision = await self.gate(PREFIX + short, data, None)
        if decision.__class__.__name__ == "PermissionResultDeny":
            return None
        if short == "choose_card_sections":  # use the user's own ticks
            args = (decision.updated_input.get("sections", []),)
        if short == "confirm_helping_someone":  # the user's own answer
            args = (args[0], decision.updated_input.get("agreed", False))
        try:
            result = fn(*args)
            if self.state.trace:
                self.trace(f"  [agent] {self.state.trace[-1]}")
            return result
        except ToolError as e:
            self.trace(f"  [rule]  blocked: {e}")
            return None

    async def _best(self, need: str) -> str:
        """Add the top choice the app's rules give for this need: a free service, then a simple
        preparation, then any other listed service. Returns the line that describes it."""
        b = self.box
        found = await self._call("find_services", b.find_services, need)
        services = (found or {}).get("services", [])
        free = [s for s in services if s.get("cost") == "free"]
        label = LABELS.get(need, need.replace("_", " ").capitalize())
        if not free:
            preps = ((await self._call("find_preparations", b.find_preparations, need)) or {}).get("preparations", [])
            if preps:
                await self._call("add_to_plan", b.add_to_plan, need, "preparation", preps[0]["id"])
                return f"- **{label}:** {preps[0]['name']}. {preps[0]['what']}"
        pick = (free or services or [None])[0]
        if not pick:
            await self._call("add_to_plan", b.add_to_plan, need, "blank")
            return f"- **{label}:** nothing on our list yet. Boston's Council on Aging or Mass 211 can help."
        await self._call("add_to_plan", b.add_to_plan, need, "service", pick["id"])
        line = f"- **{label}:** {pick['name']}" + (" is free." if pick.get("cost") == "free" else ".")
        line += f" {pick['how_to_use']}" + (f" Call {pick['phone']}." if pick.get("phone") else "")
        if need == "ride_home_after_procedure":
            line += " Please ask your surgical team if this meets their rule for who takes you home."
        return line

    async def _next_card_step(self) -> str:
        """Ask about the next section the user ticked, or make the card."""
        questions = {"name": "What name would you like at the top of your card?",
                     "contacts": "Who would you like on your card, and what are their phone numbers?",
                     "medications": "Which medications would you like listed? Please use your own words.",
                     "allergies": "What allergies would you like listed? Please use your own words.",
                     "pet": "Tell me about your pet: their name, and who looks after them.",
                     "papers": "Where do you keep your important papers? Just the place, not account numbers."}
        if self.queue:
            return questions[self.queue[0]]
        self.turn = 9
        st = self.state
        me = re.search(r"\b(?:I'm|I am|my name is)\s+([A-Z][a-z]+)", st.heard())
        name = (self.name or (me.group(1) if me else "")) if "name" in (st.card_sections or []) else ""
        card = await self._call("make_fridge_card", self.box.make_fridge_card, name,
                                self.contacts, self.meds, self.pet, "", self.allergies, self.papers)
        if card is None:
            return "Of course. What would you like to change on the card?"
        return ("Your fridge card is ready. Press **See your fridge card** to look at it and print it.\n\n"
                "Let's look at this plan again in a few months.")

    async def _maybe_suggest(self, text: str) -> None:
        """Only for keeping people updated, or many people helping over time: a link the person may choose.
        It comes from the services list, and it is never added to the plan for them."""
        if self.suggested or not UPDATES.search(text):
            return
        found = await self._call("find_services", self.box.find_services, "family_updates")
        pick = next(iter((found or {}).get("services", [])), None)
        if pick:
            self.suggested = True
            self.suggestion = {"name": pick["name"], "url": pick["url"], "what": pick["how_to_use"],
                               "free": pick["cost"] == "free", "separate": separate_service_line(pick["name"]),
                               "simpler": "For one or two small tasks, the helper list on this page is simpler."}

    async def send(self, user_text: str, on_text=print, context: str = "") -> str:  # context: live agent only
        self.transcript.append(("user", user_text))
        self.state.user_said.append(user_text)
        if not CRISIS.search(user_text):
            await self._maybe_suggest(user_text)
        reply = await self._script(user_text)
        on_text(reply)
        self.transcript.append(("agent", reply))
        return reply

    # ---- the demo script (uses only what the user typed) ----------------------
    async def _script(self, text: str) -> str:
        b, st = self.box, self.state
        if CRISIS.search(text):
            await self._call("get_support_lines", b.get_support_lines)
            return ("I'm really glad you told me. That sounds lonely, and it matters.\n\n"
                    "Is there someone you trust you could reach out to today? You can also "
                    "call or text **988** any time to talk with a caring person. In "
                    "Massachusetts, **Call2Talk** is there to listen too: dial 2-1-1 and ask "
                    "for Call2Talk.\n\n"
                    "If you'd ever like someone to check on you, a wellness check is an option. "
                    "In Boston, call 617-343-4200. We can set the plan aside. I'm here when you "
                    "want to come back to it.")

        if self.turn == 0 and (st.waiting_for_agreement() or (HELPING.search(text) and not st.helping_someone)):
            out = await self._call("confirm_helping_someone", b.confirm_helping_someone,
                                   next(iter(names_in(text)), ""))
            if not out or not out["agreed"]:
                return out["tell_the_user"] if out else "Of course. Tell me a little more."
            if not any(re.search(pat, text, re.I) for _, pat in NEED_WORDS):
                return (out["tell_the_user"] + "\n\nWhat's coming up for them? For example, a procedure, "
                        "a hospital stay, or getting ready for winter.")

        if self.turn == 0:
            needs = [n for n, pat in NEED_WORDS if re.search(pat, text, re.I)]
            if not needs:
                # Never ask the exact same thing twice: the second time, say what this demo understands.
                self.misses = getattr(self, "misses", 0) + 1
                if self.misses == 1:
                    return ("Thanks for telling me. What's coming up? For example, a procedure, a "
                            "hospital stay, or getting ready for winter.")
                return (f"This planner can help with {TOPICS}. You can tap one of the sentences, "
                        "or say something like \"I have a procedure next month\".")
            if "ride_home_after_procedure" in needs:
                needs.insert(1, "spare_key_access")
            self.turn = 1
            ok = await self._call("confirm_needs", b.confirm_needs, needs)
            if ok is None:
                self.turn = 0
                return "Of course. Which parts would you like to plan for?"
            # Two voices: the note and table are in the user's words ("me"); the question is ours ("you").
            pet = "pet_care_during_stay" in needs
            self.task = ("look after my pet while I'm away" if pet
                         else "check in on me the evening after my procedure")
            if st.helping_someone:  # planning for someone else: talk about them as "they"
                task_you = ("look after their pet while they're away" if pet
                            else "check in on them the evening after their procedure")
                return f"Thank you. Is there someone they'd feel comfortable asking to {task_you}? What's their name?"
            task_you = ("look after your pet while you're away" if pet
                        else "check in on you the evening after your procedure")
            return f"Thank you. Is there someone you'd feel comfortable asking to {task_you}? What's their name?"

        if self.turn == 1:
            self.turn = 2
            no_one = bool(NO_ONE.search(text))
            burden = bool(BURDEN.search(text)) or bool(NO.search(text)) or no_one
            names = [] if burden else names_in(text)
            await self._call("set_comfort", b.set_comfort, bool(names), bool(BURDEN.search(text)), no_one)
            lines = []
            for need in st.confirmed_needs:
                if need == "pet_care_during_stay" and names:   # a friend the visitor named
                    await self._call("add_to_plan", b.add_to_plan, need, "person")
                    lines.append(f"- **Your pet:** {' and '.join(names)}.")
                else:
                    lines.append(await self._best(need))      # the app's own rules pick it, free first
            if names:
                who = " and ".join(names)
                note = (f"Hi {who}. I'm making a small plan in case I need a hand. Would you be up "
                        f"to {self.task}? No pressure at all, and no need to reply if it doesn't suit you.")
                sent = await self._call("draft_helper_note", b.draft_helper_note, names, self.task, note)
                if sent:
                    for n in names:
                        await self._call("add_helper_task", b.add_helper_task, self.task[0].upper() + self.task[1:], n, "", "")
                    lines.append(f"- **Note:** I wrote a short note to {who}. You can send it yourself.")
            if no_one:
                lines.append("- " + NO_ONE_LINE)
            await self._call("choose_card_sections", b.choose_card_sections, [])
            picked = st.card_sections or []
            self.queue = [k for k in ("contacts", "medications", "allergies", "pet", "papers") if k in picked]
            if "name" in picked and not re.search(r"\b(?:I'm|I am|my name is)\s+[A-Z]", st.heard()):
                self.queue.insert(0, "name")
            return "Here's what I found.\n\n" + "\n".join(lines) + "\n\n" + await self._next_card_step()

        if self.turn == 2:
            current = self.queue.pop(0) if self.queue else None
            if current == "name":
                found = re.findall(r"\b[A-Z][a-z]+\b", text) or [text.strip().split(" ")[0].capitalize()]
                self.name = found[0] if not NO.search(text) else ""
            elif current == "contacts" and not NO.search(text):
                self.contacts = [{"name": n, "relationship": "", "phone": ph}
                                 for n, ph in re.findall(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\D{0,20}?(" + PHONE + ")", text)]
            elif current == "medications" and not NO.search(text):
                self.meds = [m.strip(" .") for m in re.split(r"[;,\n]|\band\b", text) if m.strip(" .")]
            elif current == "allergies" and not NO.search(text):
                self.allergies = [a.strip(" .") for a in re.split(r"[;,\n]|\band\b", text) if a.strip(" .")]
            elif current == "pet" and not NO.search(text):
                self.pet = text.strip(" .")[:60]
            elif current == "papers" and not NO.search(text):
                self.papers = text.strip(" .")[:120]
            return await self._next_card_step()

        return ("Your plan is ready. You can say yes or no to each part, print your fridge card, or make "
                "a readiness summary. To plan for something else, reload this page and start a new plan.")
