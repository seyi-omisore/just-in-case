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

CRISIS = re.compile(r"notice|who would (even )?know|no one|nobody|point anymore", re.I)
BURDEN = re.compile(r"bother|impose|burden|rather not|don'?t want to ask", re.I)


class DemoAgent:
    def __init__(self, approver, trace=print, on_tool=None, directory: Directory | None = None):
        self.state = PlanState(town="Boston")
        self.box = Toolbox(self.state, directory or Directory())
        self.trace = trace
        self.on_tool = on_tool or (lambda *_: None)
        self.gate = make_gate(approver, trace)
        self.turn = 0
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
        reply = await self._script(user_text)
        on_text(reply)
        self.transcript.append(("agent", reply))
        return reply

    # ---- the Ruth script ----------------------------------------------------
    async def _script(self, text: str) -> str:
        b = self.box
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

        self.turn += 1
        if self.turn == 1:
            needs = ["ride_home_after_procedure", "spare_key_access",
                     "pet_care_during_stay", "mail_while_away"]
            ok = await self._call("confirm_needs", b.confirm_needs, needs)
            if ok is None:
                return "No problem. Which parts would you like to plan for?"
            return ("Thank you. Fifi will need someone while you're away.\n\n"
                    "Would you feel comfortable asking a friend to feed Fifi?")

        if self.turn == 2:
            burden = bool(BURDEN.search(text))
            await self._call("set_comfort", b.set_comfort, not burden, burden)
            await self._call("find_services", b.find_services, "ride_home_after_procedure")
            await self._call("add_to_plan", b.add_to_plan, "ride_home_after_procedure",
                             "service", "svc_fw_escort")
            await self._call("find_preparations", b.find_preparations, "spare_key_access")
            await self._call("add_to_plan", b.add_to_plan, "spare_key_access",
                             "preparation", "prep_key_lockbox")
            await self._call("find_preparations", b.find_preparations, "mail_while_away")
            await self._call("add_to_plan", b.add_to_plan, "mail_while_away",
                             "preparation", "prep_usps_hold_mail")
            await self._call("find_services", b.find_services, "pet_care_during_stay")
            if burden:
                await self._call("add_to_plan", b.add_to_plan, "pet_care_during_stay",
                                 "preparation", "prep_pet_sheet")
                pet_line = ("For Fifi, a pet care sheet on the fridge will help anyone who "
                            "steps in. You can also call MSPCA-Angell to ask about short stays.")
            else:
                await self._call("add_to_plan", b.add_to_plan, "pet_care_during_stay", "person")
                note = ("Hi Marion and Doris. I have a procedure next month and may be in the "
                        "hospital for two nights. Would one of you be up for feeding Fifi while "
                        "I'm away? No pressure at all, and no need to reply if it doesn't suit you. "
                        "Love, Ruth")
                sent = await self._call("draft_helper_note", b.draft_helper_note,
                                        ["Marion", "Doris"], "Feed Fifi while Ruth is away", note)
                pet_line = ("I wrote a short note to Marion and Doris about Fifi. You can send it "
                            "yourself if it looks right." if sent else
                            "We can write the note to your friends together whenever you like.")
            return ("Here's your plan so far.\n\n"
                    "- **Getting home:** FriendshipWorks is free. A volunteer goes with you and "
                    "helps you get home. Call 617-482-1510 to book. Please ask your surgical team "
                    "if this meets their rule for who takes you home.\n"
                    "- **Spare key:** a small lockbox by the door.\n"
                    "- **Mail:** USPS Hold Mail is free. Sign in at USPS.com a week or more early.\n"
                    f"- **Fifi:** {pet_line}\n\n"
                    "Who would you like on your fridge card, and what are their phone numbers?")

        if self.turn == 3:
            contacts = [{"name": "Marion Kelly", "relationship": "friend", "phone": "617-555-0142"},
                        {"name": "Doris Chen", "relationship": "neighbor", "phone": "617-555-0177"}]
            meds = ["Blood pressure pill, mornings", "Eye drops, at night"]
            card = await self._call("make_fridge_card", b.make_fridge_card, "Ruth", contacts,
                                    meds, "Fifi (cat)", "Doris Chen")
            if card is None:
                return "Of course. What would you like to change on the card?"
            return ("Your fridge card is ready. You can print it and put it on the fridge.\n\n"
                    "Let's look at this plan again in a few months. And please tell Marion or "
                    "Doris at least two weeks before your procedure.")

        return ("This is the demo version, so it follows a set story. Turn on the real agent "
                "to keep talking.")
