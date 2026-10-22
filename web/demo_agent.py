"""A scripted stand-in for the real agent. No AI, no internet, no cost.

Used for:
  - testing the website without spending API money (python serve.py --demo)
  - the presentation backup (replay mode, later step)

It is NOT the product. It follows a fixed Ruth script, but it calls the REAL
tools and the REAL approval gate, so every rule and checkpoint behaves exactly
as it does with the live agent. The website shows a "Demo mode" banner.
"""

import asyncio
import re

from just_in_case.agent import make_gate
from just_in_case.directory import Directory
from just_in_case.state import PlanState
from just_in_case.tools import PREFIX, Toolbox, ToolError

PAUSE = 0.5  # seconds between steps, so the activity feed is readable

CRISIS = re.compile(r"notice|who would (even )?know|no one would|nobody would|point anymore", re.I)
BURDEN = re.compile(r"bother|impose|burden|rather not|don'?t want to ask|no one to ask", re.I)
NO = re.compile(r"^\s*(no|nope|none|not really|nothing)\b", re.I)

# Keywords -> needs. The demo only plans for what the user brings up.
NEED_WORDS = [
    ("ride_home_after_procedure", r"procedure|surgery|sedation|operation|take me home|ride home"),
    ("pet_care_during_stay", r"\bpets?\b|\bcats?\b|\bdogs?\b|\bbird\b|kitten|puppy"),
    ("mail_while_away", r"hospital|away|stay|nights?\b|trip|travel"),
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


class DemoAgent:
    def __init__(self, approver, trace=print, on_tool=None, directory: Directory | None = None,
                 out_dir=None):
        self.state = PlanState(town="Boston")
        self.box = Toolbox(self.state, directory or Directory(),
                           **({"out_dir": out_dir} if out_dir else {}))
        self.trace = trace
        self.on_tool = on_tool or (lambda *_: None)
        self.gate = make_gate(approver, trace)
        self.turn = 0
        self.task = ""
        self.contacts: list[dict] = []
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
                    "pet_name": args[3], "pet_caregiver": args[4]}
        elif short in ("find_services", "find_preparations"):
            data = {"need": args[0]}
        elif short == "add_to_plan":
            data = {"need": args[0], "route": args[1]}
        elif short == "add_helper_task":
            data = {"task": args[0], "person": args[1], "date": args[2], "time": args[3]}
        self.on_tool(short, data)
        await asyncio.sleep(PAUSE)
        decision = await self.gate(PREFIX + short, data, None)
        if decision.__class__.__name__ == "PermissionResultDeny":
            return None
        try:
            result = fn(*args)
            if self.state.trace:
                self.trace(f"  [agent] {self.state.trace[-1]}")
            return result
        except ToolError as e:
            self.trace(f"  [rule]  blocked: {e}")
            return None

    async def send(self, user_text: str, on_text=print) -> str:
        self.transcript.append(("user", user_text))
        self.state.user_said.append(user_text)
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

        if self.turn == 0:
            needs = [n for n, pat in NEED_WORDS if re.search(pat, text, re.I)]
            if not needs:
                return ("Thanks for telling me. What's coming up? For example, a procedure, a "
                        "hospital stay, or getting ready for winter.")
            if "ride_home_after_procedure" in needs:
                needs.insert(1, "spare_key_access")
            self.turn = 1
            ok = await self._call("confirm_needs", b.confirm_needs, needs)
            if ok is None:
                self.turn = 0
                return "Of course. Which parts would you like to plan for?"
            self.task = ("look after my pet while I'm away" if "pet_care_during_stay" in needs
                         else "check in on me the evening after my procedure")
            return f"Thank you. Is there someone you'd feel comfortable asking to {self.task}? What's their name?"

        if self.turn == 1:
            self.turn = 2
            burden = bool(BURDEN.search(text)) or bool(NO.search(text))
            names = [] if burden else names_in(text)
            await self._call("set_comfort", b.set_comfort, bool(names), bool(BURDEN.search(text)))
            lines = []
            for need in st.confirmed_needs:
                if need == "ride_home_after_procedure":
                    await self._call("find_services", b.find_services, need)
                    await self._call("add_to_plan", b.add_to_plan, need, "service", "svc_fw_escort")
                    lines.append("- **Getting home:** FriendshipWorks is free. A volunteer goes with you "
                                 "and helps you get home. Call 617-482-1510 to book. Please ask your "
                                 "surgical team if this meets their rule for who takes you home.")
                elif need == "spare_key_access":
                    await self._call("find_preparations", b.find_preparations, need)
                    await self._call("add_to_plan", b.add_to_plan, need, "preparation", "prep_key_lockbox")
                    lines.append("- **Spare key:** a small lockbox by the door.")
                elif need == "mail_while_away":
                    await self._call("find_preparations", b.find_preparations, need)
                    await self._call("add_to_plan", b.add_to_plan, need, "preparation", "prep_usps_hold_mail")
                    lines.append("- **Mail:** USPS Hold Mail is free. Sign in at USPS.com a week or more early.")
                elif need == "power_outage":
                    await self._call("find_services", b.find_services, need)
                    await self._call("add_to_plan", b.add_to_plan, need, "service", "svc_eversource_medical")
                    lines.append("- **Power:** ask your electric company to note any medical equipment on your account.")
                elif need == "pet_care_during_stay":
                    if names:
                        await self._call("add_to_plan", b.add_to_plan, need, "person")
                        lines.append(f"- **Your pet:** {' and '.join(names)}.")
                    else:
                        await self._call("find_preparations", b.find_preparations, need)
                        await self._call("add_to_plan", b.add_to_plan, need, "preparation", "prep_pet_sheet")
                        lines.append("- **Your pet:** a pet care sheet on the fridge helps anyone who steps in.")
            if names:
                who = " and ".join(names)
                note = (f"Hi {who}. I'm making a small plan in case I need a hand. Would you be up "
                        f"to {self.task}? No pressure at all, and no need to reply if it doesn't suit you.")
                sent = await self._call("draft_helper_note", b.draft_helper_note, names, self.task, note)
                if sent:
                    for n in names:
                        await self._call("add_helper_task", b.add_helper_task, self.task[0].upper() + self.task[1:], n, "", "")
                    lines.append(f"- **Note:** I wrote a short note to {who}. You can send it yourself.")
            return ("Here's your plan so far.\n\n" + "\n".join(lines) +
                    "\n\nNow your fridge card. Who would you like on it, and what are their phone numbers?")

        if self.turn == 2:
            self.turn = 3
            self.contacts = [] if NO.search(text) else [
                {"name": n, "relationship": "", "phone": ph}
                for n, ph in re.findall(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\D{0,20}?(" + PHONE + ")", text)]
            return "Are there any medications you'd like listed on the card? You can say no."

        if self.turn == 3:
            self.turn = 4
            meds = [] if NO.search(text) else [m.strip(" .") for m in re.split(r"[;,\n]|\band\b", text) if m.strip(" .")]
            me = re.search(r"\b(?:I'm|I am|my name is)\s+([A-Z][a-z]+)", st.heard())
            card = await self._call("make_fridge_card", b.make_fridge_card, me.group(1) if me else "",
                                    self.contacts, meds, "", "")
            if card is None:
                self.turn = 3
                return "Of course. What would you like to change on the card?"
            return ("Your fridge card is ready. You can print it and put it on the fridge.\n\n"
                    "Let's look at this plan again in a few months.")

        return ("This is the demo version, so it follows a set path. Turn on the real agent "
                "to keep talking.")
