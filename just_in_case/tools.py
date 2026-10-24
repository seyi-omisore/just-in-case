"""The agent's tools.

Two layers:
  1. Toolbox: plain Python methods holding all the logic and rules. Easy to
     unit test without spending any API money.
  2. build_server(): thin @tool wrappers that expose those methods to the
     model through the Claude Agent SDK as an in-process MCP server.

Tool names the model sees look like  mcp__jic__find_services
("mcp" + server name "jic" + tool name).
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import timeline
from .card import write_card
from .config import MAX_HELPERS, OUTPUT_DIR
from .directory import Directory
from .safeguards import (ESCORT_RULE_LINE, FALLBACK_LINE, NO_ONE_LINE, check_helper_note, safe_cell,
                         said_by_user)
from .state import CARD_SECTIONS, NEEDS, ROUTES, PlanItem, PlanState

SERVER_NAME = "jic"
PREFIX = f"mcp__{SERVER_NAME}__"

# Tools that pause for the user's approval (the three human checkpoints).
GATED_TOOLS = {"confirm_needs", "draft_helper_note", "add_helper_task",
               "choose_card_sections", "make_fridge_card", "confirm_helping_someone"}
NOT_AGREED = ("The person this plan is for has not agreed yet. Do not plan. Kindly suggest the user "
              "talks with them first, and ask the agreement checkpoint again only when the user says they agree.")
TASK_FIELDS = ("task", "date", "time", "person")
MAX_FIELD = 120
# A helper task is Open (no name), Someone's helping (a name), or Confirmed (the owner checked).
TASK_STATUS = {"open": "Open", "helping": "Someone's helping", "confirmed": "Confirmed"}
MAX_SAVED_SAID = 200   # messages kept in a saved plan
MAX_SAVED_TASKS = 50


class ToolError(Exception):
    """Raised for a rule violation. The message goes back to the model."""


def task_status(t: dict) -> str:
    if not t.get("person"):
        return "open"
    return "confirmed" if t.get("confirmed") else "helping"


class Toolbox:
    def __init__(self, state: PlanState, directory: Directory, out_dir: Path = OUTPUT_DIR,
                 today: date | None = None):
        self.state = state
        self.dir = directory
        self.out_dir = out_dir
        self.today = today or date.today()

    # ---- planning for someone else ------------------------------------------
    def confirm_helping_someone(self, person_name: str = "", agreed: bool = False) -> dict:
        """CHECKPOINT. The user is planning for someone else. `agreed` is the user's
        own answer from the page ("Yes, they agree" / "Not yet"), never the agent's."""
        s = self.state
        s.helping_someone, s.person_agreed = True, bool(agreed)
        name = str(person_name or "").strip()[:60]
        s.planning_for = name if said_by_user(name, s.heard(), name=True) else ""
        s.log(f"checkpoint: planning for someone else, agreed={s.person_agreed}")
        who = s.planning_for or "the person you're helping"
        if not s.person_agreed:
            return {"agreed": False, "next": NOT_AGREED,
                    "tell_the_user": "That's okay. Plans work best when the person helps make them. "
                                     f"You might talk with {who} first. I'm here when you're both ready."}
        return {"agreed": True,
                "tell_the_user": f"Thank you. Let's plan together. {who[0].upper() + who[1:]} decides what goes "
                                 "in the plan and who is asked to help.",
                "next": "Talk about the person as 'they'. Ask what is coming up for them."}

    # ---- checkpoint 1 -----------------------------------------------------
    def confirm_needs(self, needs: list[str]) -> dict:
        if self.state.waiting_for_agreement():
            raise ToolError(NOT_AGREED)
        unknown = [n for n in needs if n not in NEEDS]
        if unknown:
            raise ToolError(f"Unknown needs {unknown}. Use only: {list(NEEDS)}")
        self.state.confirmed_needs = list(dict.fromkeys(needs))
        if not self.state.started and self.state.confirmed_needs:
            self.state.started = self.today.isoformat()  # the day the plan began
        self.state.log(f"checkpoint 1: user confirmed needs {self.state.confirmed_needs}")
        return {"confirmed_needs": self.state.confirmed_needs}

    def set_comfort(self, comfortable_asking_people: bool, worried_about_burden: bool,
                    no_one_to_ask: bool = False) -> dict:
        s = self.state
        s.comfortable_asking_people = comfortable_asking_people and not no_one_to_ask
        # Once she says she worries about being a burden, it sticks for the session.
        s.worried_about_burden = s.worried_about_burden or worried_about_burden
        s.no_one_to_ask = s.no_one_to_ask or no_one_to_ask
        s.log(f"comfort: asking_people={s.comfortable_asking_people} burden={s.worried_about_burden} "
              f"no_one={s.no_one_to_ask}")
        out = {"people_allowed": s.people_allowed(), "burden_mode": s.worried_about_burden}
        if s.no_one_to_ask:
            out["services_only"] = "Plan with services and preparations only. Do not ask about people again."
            out["tell_the_user"] = NO_ONE_LINE
        return out

    # ---- lookups ------------------------------------------------------------
    def find_services(self, need: str) -> dict:
        self._check_need(need)
        results = self.dir.find_services(need, self.state.town)
        self.state.log(f"find_services({need}) -> {[r['id'] for r in results]}")
        return {"need": need, "town": self.state.town, "services": results,
                "note": "Ranked free first. Only recommend services from this list."}

    def find_preparations(self, need: str) -> dict:
        self._check_need(need)
        results = self.dir.find_preparations(need)
        self.state.log(f"find_preparations({need}) -> {[r['id'] for r in results]}")
        return {"need": need, "preparations": results}

    # ---- the decision ladder ------------------------------------------------
    def add_to_plan(self, need: str, route: str, ref_id: str | None = None) -> dict:
        s = self.state
        self._check_need(need)
        if s.waiting_for_agreement():
            raise ToolError(NOT_AGREED)
        if need not in s.confirmed_needs:
            raise ToolError("Confirm this need with the user first (confirm_needs).")
        if route not in ROUTES:
            raise ToolError(f"route must be one of {ROUTES}")

        flags: list[str] = []
        if route == "service":
            if not ref_id or not self.dir.is_service(ref_id):
                raise ToolError(f"'{ref_id}' is not in the service directory. Do not invent services.")
            svc = self.dir.by_id[ref_id]
            if not any(r["id"] == ref_id for r in self.dir.find_services(need, s.town)):
                raise ToolError(f"{svc['name']} does not cover '{need}' in {s.town}.")
            label = svc["name"]
            flags += self.dir.service_flags(svc)
        elif route == "preparation":
            if not ref_id or not self.dir.is_preparation(ref_id):
                raise ToolError(f"'{ref_id}' is not a known preparation.")
            label = self.dir.by_id[ref_id]["name"]
        elif route == "person":
            if not s.people_allowed():
                raise ToolError(
                    "People are not part of this plan (the user has not said she is "
                    "comfortable asking, or she worries about being a burden). Use a "
                    "service, a preparation, or leave it blank."
                )
            label = "Someone you chose (see your note)"
            ref_id = None
        else:  # blank
            label = "Not covered yet"
            ref_id = None
            flags.append(FALLBACK_LINE)

        if need == "ride_home_after_procedure" and route != "blank":
            flags.append(ESCORT_RULE_LINE)

        # One route per need: replace any earlier choice.
        s.items = [i for i in s.items if i.need != need]
        s.items.append(PlanItem(need=need, route=route, ref_id=ref_id, label=label, flags=flags))
        s.log(f"add_to_plan({need}) -> {route}:{ref_id or '-'}")
        return {"added": {"need": need, "route": route, "label": label, "flags": flags},
                "tell_the_user": flags}

    # ---- checkpoint 2 -------------------------------------------------------
    def _note_problem(self, helper_names: list[str], note_text: str) -> str | None:
        """Why a note cannot be drafted, or None. Checked BEFORE the user is asked."""
        s = self.state
        if not helper_names:
            return "Ask the user who they would like to write to."
        heard = s.heard()
        unheard = [n for n in helper_names if not said_by_user(n, heard, name=True)]
        if unheard:
            return f"The user has not named {', '.join(unheard)}. Ask who they would like to write to."
        # Asking for a note to someone by name IS saying they are comfortable asking.
        latest = s.user_said[-1] if s.user_said else ""
        asked_now = all(said_by_user(n, latest, name=True) for n in helper_names)
        if not (asked_now or s.people_allowed()):
            return ("First ask whether the user would like to ask someone for help, and who. "
                    "Do not tell the user you cannot write notes.")
        if s.helper_count() + len(helper_names) > MAX_HELPERS:
            return f"Keep the plan small: at most {MAX_HELPERS} people in total."
        return check_helper_note(note_text)

    def draft_helper_note(self, helper_names: list[str], role: str, note_text: str) -> dict:
        s = self.state
        problem = self._note_problem(helper_names, note_text)
        if problem:
            raise ToolError(problem)
        latest = s.user_said[-1] if s.user_said else ""
        if all(said_by_user(n, latest, name=True) for n in helper_names) and not s.people_allowed():
            # She asked for this herself, so she has changed her mind about asking someone.
            s.comfortable_asking_people, s.worried_about_burden, s.no_one_to_ask = True, False, False
            s.log("comfort: user asked for a note to someone by name")
        s.helper_notes.append({"helpers": helper_names, "role": role, "text": note_text})
        s.log(f"checkpoint 2: note approved for {helper_names}")
        return {"status": "Draft saved. NOT sent. The user sends it herself if she wants to.",
                "note_text": note_text}

    # ---- checkpoint 3 -------------------------------------------------------
    def choose_card_sections(self, sections: list[str]) -> dict:
        """CHECKPOINT. The user ticks what goes on the card. The list that arrives
        here is the user's own choice from the checklist, not the agent's guess."""
        bad = [x for x in sections if x not in CARD_SECTIONS]
        if bad:
            raise ToolError(f"Unknown card sections {bad}. Use only: {list(CARD_SECTIONS)}")
        self.state.card_sections = list(dict.fromkeys(sections))
        self.state.log(f"checkpoint: card sections chosen {self.state.card_sections}")
        picked = [CARD_SECTIONS[x] for x in self.state.card_sections]
        return {"user_picked": self.state.card_sections,
                "next": ("Ask only about these, one at a time: " + ", ".join(picked)) if picked
                else "The user picked nothing extra. Make the card with just the emergency lines."}

    def _card_problem(self, name: str, contacts: list[dict], medications: list[str],
                      pet_name: str = "", pet_caregiver: str = "",
                      allergies: list[str] | None = None, papers: str = "") -> str | None:
        """Why this card cannot be made, or None. Checked BEFORE the user is asked."""
        chosen = self.state.card_sections
        if chosen is None:
            return "First ask what the user wants on the card: call choose_card_sections."
        allergies = allergies or []
        not_chosen = [CARD_SECTIONS[key] for key, given in
                      (("name", name), ("contacts", contacts), ("medications", medications),
                       ("allergies", allergies), ("pet", pet_name), ("papers", papers))
                      if given and key not in chosen]
        if not_chosen:
            return ("The user did not pick these for the card: " + ", ".join(not_chosen)
                    + ". Leave them off and do not mention them.")
        heard = self.state.heard()
        missing = []
        if name and not said_by_user(name, heard, name=True):
            missing.append(f"the name '{name}'")
        for c in contacts:
            if not said_by_user(c.get("name", ""), heard, name=True):
                missing.append(f"contact '{c.get('name')}'")
            if not said_by_user(c.get("phone", ""), heard):
                missing.append(f"phone number '{c.get('phone')}'")
        for m in medications:
            if not said_by_user(m, heard):
                missing.append(f"medication '{m}'")
        for a in allergies:
            if not said_by_user(a, heard):
                missing.append(f"allergy '{a}'")
        if papers and not said_by_user(papers, heard):
            missing.append(f"where papers are kept '{papers}'")
        if pet_name and not said_by_user(pet_name, heard):
            missing.append(f"pet '{pet_name}'")
        if pet_caregiver and not said_by_user(pet_caregiver, heard, name=True):
            missing.append(f"pet caregiver '{pet_caregiver}'")
        if missing:
            return ("The card can only use details the user typed. Not said by the user: "
                    + "; ".join(missing) + ". Ask the user instead of filling it in, or leave it off.")
        return None

    def make_fridge_card(self, name: str, contacts: list[dict], medications: list[str],
                         pet_name: str = "", pet_caregiver: str = "",
                         allergies: list[str] | None = None, papers: str = "") -> dict:
        problem = self._card_problem(name, contacts, medications, pet_name, pet_caregiver, allergies, papers)
        if problem:
            raise ToolError(problem)
        card = {"name": name, "contacts": contacts, "medications": medications,
                "allergies": allergies or [], "papers": papers,
                "pet": {"name": pet_name, "caregiver": pet_caregiver} if pet_name else None,
                "sections": list(self.state.card_sections)}
        self.card = card  # kept so the card can refresh when the plan or table changes
        path = write_card(card, self.state, self.dir, self.today, self.out_dir)
        self.state.card_path = str(path)
        self.state.log(f"checkpoint 3: card approved -> {path.name}")
        # No file paths for the user: the page has a button.
        return {"status": "Card made.",
                "tell_the_user": "Your card is ready. Press 'See your fridge card' on the page to look at it "
                                 "and print it. Once printed, it works without Wi-Fi.",
                "on_the_card": [CARD_SECTIONS[k] for k in self.state.card_sections]}

    # ---- helper table ---------------------------------------------------------
    def _clean_task(self, row: dict) -> dict:
        out = {k: str(row.get(k, "") or "").strip()[:MAX_FIELD] for k in TASK_FIELDS}
        if not out["task"]:
            raise ToolError("Each row needs a task.")
        return out  # person may be blank: an open task family can sign up for

    def _task_problem(self, task: str, person: str = "") -> str | None:
        s = self.state
        if not str(task or "").strip():
            return "Each row needs a task."
        if not s.people_allowed():
            return "The user has not said they would like help from others, so do not add rows. Ask first."
        if person and not said_by_user(person, s.heard(), name=True):
            return f"The user has not named {person}. Leave the person blank instead."
        people = {t["person"].lower() for t in s.helper_tasks if t["person"]} | ({person.lower()} if person else set())
        if len(people) > MAX_HELPERS:
            return f"Keep the plan small: at most {MAX_HELPERS} people in the table."
        return None

    def add_helper_task(self, task: str, person: str = "", date: str = "", time: str = "") -> dict:
        """Agent adds a row (checkpoint). Leave person blank for an open task
        family can sign up for. A named person must be someone the user named."""
        s = self.state
        problem = self._task_problem(task, person)
        if problem:
            raise ToolError(problem)
        row = self._clean_task({"task": task, "person": person, "date": date, "time": time})
        row["id"] = s.next_task_id
        s.next_task_id += 1
        s.helper_tasks.append(row)
        s.log(f"checkpoint: helper row added for {row['person'] or 'anyone (open)'}")
        self.refresh_card()
        return {"added": row, "note": "The user can edit or remove this row any time."}

    def precheck(self, short: str, data: dict) -> str | None:
        """Run the rules for a checkpoint before the user is asked to approve it,
        so the user never approves something that is then refused."""
        if short == "draft_helper_note":
            return self._note_problem(data.get("helper_names", []), data.get("note_text", ""))
        if short == "add_helper_task":
            return self._task_problem(data.get("task", ""), data.get("person", ""))
        if short == "make_fridge_card":
            return self._card_problem(data.get("name", ""), data.get("contacts", []),
                                      data.get("medications", []), data.get("pet_name", ""),
                                      data.get("pet_caregiver", ""), data.get("allergies", []),
                                      data.get("papers", ""))
        if short == "confirm_needs":
            if self.state.waiting_for_agreement():
                return NOT_AGREED
            unknown = [n for n in data.get("needs", []) if n not in NEEDS]
            return f"Unknown needs {unknown}. Use only: {list(NEEDS)}" if unknown else None
        return None

    def user_confirm_item(self, need: str, ok: bool) -> None:
        """The user answers a plan item on the page: "Yes, add this" or "No thanks"."""
        s = self.state
        item = next((i for i in s.items if i.need == need), None)
        if item is None or item.route == "blank":
            raise ToolError("That part of the plan is not there anymore.")
        if ok:
            item.user_ok = True
        else:
            s.items = [i for i in s.items if i.need != need]  # back to "still to plan"
        s.log(f"user {'confirmed' if ok else 'said no thanks to'} {need}")
        self.refresh_card()

    def user_next_step(self, need: str, action: str) -> None:
        """The user answers the "next step" card: Done, or Remind me later."""
        s = self.state
        item = next((i for i in s.items if i.need == need), None)
        if item is None or item.route == "blank" or not item.user_ok or action not in ("done", "later"):
            raise ToolError("That step is not on your plan anymore.")
        s.actions_later = [n for n in s.actions_later if n != need]
        if action == "done":
            s.actions_done.add(need)
        else:
            s.actions_later.append(need)  # goes to the end of the line
        s.log(f"user marked the step for {need}: {action}")

    def user_review(self, action: str) -> None:
        """The user marks their plan as reviewed today, or takes back today's mark."""
        from . import reviews
        s = self.state
        today = self.today.isoformat()
        if action == "mark":
            if not any(i.user_ok and i.route != "blank" for i in s.items):
                raise ToolError("Add something to your plan first, then you can mark it as reviewed.")
            if today not in s.reviews:  # one mark per day is enough
                s.reviews.append(today)
                s.reviews = s.reviews[-reviews.MAX_REVIEWS:]
        elif action == "undo":
            if today not in s.reviews:
                raise ToolError("There is no review from today to take back.")
            s.reviews.remove(today)
        else:
            raise ToolError("Choose mark or undo.")
        s.log(f"user review: {action}")
        self.refresh_card()

    # ---- "Getting ready" timeline (the user's own date and ticks) -------------
    def user_set_event_date(self, value: str) -> None:
        if value and timeline.parse_day(value, self.today) is None:
            raise ToolError("Please pick a date within the next year.")
        self.state.event_date = str(value or "")
        self.state.log("user set the date for their timeline" if value else "user cleared the timeline date")

    def user_tick_step(self, step_id: str, done: bool) -> None:
        ids = {s["id"] for s in timeline.build(self.state, self.today)["steps"]}
        if step_id not in ids:
            raise ToolError("That step is not on the timeline anymore.")
        (self.state.steps_done.add if done else self.state.steps_done.discard)(step_id)

    def user_edit_task(self, action: str, row: dict) -> list[dict]:
        """The user edits the table on the page. Their table, their choice."""
        s = self.state
        if action == "add":
            new = self._clean_task(row)
            new["id"] = s.next_task_id
            s.next_task_id += 1
            s.helper_tasks.append(new)
        elif action == "update":
            clean = self._clean_task(row)
            for t in s.helper_tasks:
                if t["id"] == row.get("id"):
                    if clean["person"].lower() != t["person"].lower():
                        t["confirmed"] = False  # a confirmation never moves to a new name
                    t.update(clean)
        elif action == "confirm":
            t = next((t for t in s.helper_tasks if t["id"] == row.get("id")), None)
            if t is None:
                raise ToolError("That row is not in your table anymore.")
            if not t["person"]:
                raise ToolError("Add who is helping before marking it confirmed.")
            t["confirmed"] = row.get("confirmed") is True
        elif action == "delete":
            s.helper_tasks = [t for t in s.helper_tasks if t["id"] != row.get("id")]
        else:
            raise ToolError("Unknown table action.")
        s.log(f"user edited helper table ({action})")
        self.refresh_card()
        return s.helper_tasks

    # ---- save and continue later ------------------------------------------
    def export(self) -> dict:
        """Everything needed to pick up later. The user keeps it on their own device."""
        s = self.state
        return {"v": 1, "user_said": s.user_said[-MAX_SAVED_SAID:], "needs": s.confirmed_needs,
                "helping": [s.helping_someone, s.person_agreed, s.planning_for],
                "comfort": [s.comfortable_asking_people, s.worried_about_burden, s.no_one_to_ask],
                "items": [{"need": i.need, "route": i.route, "ref_id": i.ref_id, "user_ok": i.user_ok}
                          for i in s.items],
                "notes": s.helper_notes, "tasks": s.helper_tasks, "sections": s.card_sections,
                "event_date": s.event_date, "steps_done": sorted(s.steps_done),
                "actions_done": sorted(s.actions_done), "actions_later": list(s.actions_later),
                "reviews": list(s.reviews), "started": s.started,
                "card": getattr(self, "card", None)}

    def restore(self, saved: dict) -> list[str]:
        """Rebuild a saved plan by running every step through the same checks again.

        The saved copy comes from the user's browser, so it is never trusted: a
        step that does not pass (an unknown service, a person without consent, a
        card detail the user never typed) is left out. Returns what was left out.
        """
        s = self.state
        skipped: list[str] = []

        def attempt(label, step):
            # Each step reads the saved values itself, so junk is skipped, not a crash.
            try:
                step()
            except (ToolError, KeyError, TypeError, ValueError, AttributeError, IndexError):
                skipped.append(label)

        def as_list(value, limit):
            return list(value)[:limit] if isinstance(value, list) else []

        s.user_said = [str(x)[:2000] for x in as_list(saved.get("user_said"), MAX_SAVED_SAID)] + s.user_said
        helping = as_list(saved.get("helping"), 3) + [False, False, ""]
        if helping[0] is True:
            attempt("who the plan is for", lambda: self.confirm_helping_someone(str(helping[2]), helping[1] is True))
        attempt("needs", lambda: self.confirm_needs([str(n) for n in as_list(saved.get("needs"), len(NEEDS))]))
        comfort = [bool(c) for c in as_list(saved.get("comfort"), 3)] + [False] * 3
        attempt("comfort", lambda: self.set_comfort(*comfort[:3]))
        for it in as_list(saved.get("items"), len(NEEDS)):
            def add_item(it=it):
                self.add_to_plan(str(it["need"]), str(it["route"]), str(it["ref_id"]) if it.get("ref_id") else None)
                if it.get("user_ok") is True and it["route"] != "blank":
                    self.user_confirm_item(str(it["need"]), True)  # the user's own earlier "yes"
            attempt("a plan item", add_item)
        for n in as_list(saved.get("notes"), MAX_HELPERS):
            attempt("a note", lambda n=n: self.draft_helper_note(
                [str(h) for h in n["helpers"]], str(n.get("role", "")), str(n["text"])))
        for t in as_list(saved.get("tasks"), MAX_SAVED_TASKS):
            attempt("a helper row", lambda t=t: self.user_edit_task("add", {k: t.get(k, "") for k in TASK_FIELDS}))
        if saved.get("sections") is not None:
            attempt("card choices", lambda: self.choose_card_sections([str(x) for x in saved["sections"]]))
        card = saved.get("card")
        if card and s.card_sections is not None:
            def remake():
                pet = card.get("pet") or {}
                self.make_fridge_card(
                    str(card.get("name", "")), as_list(card.get("contacts"), 10),
                    [str(m) for m in as_list(card.get("medications"), 20)],
                    str(pet.get("name", "")), str(pet.get("caregiver", "")),
                    [str(a) for a in as_list(card.get("allergies"), 20)], str(card.get("papers", "")))
            attempt("the card", remake)
        attempt("the timeline date", lambda: self.user_set_event_date(str(saved.get("event_date") or "")))
        s.steps_done = {str(x)[:80] for x in as_list(saved.get("steps_done"), 100)}
        planned = {i.need for i in s.items if i.user_ok}  # only for parts the user said yes to
        s.actions_done = {str(x) for x in as_list(saved.get("actions_done"), len(NEEDS))} & planned
        s.actions_later = [str(x) for x in as_list(saved.get("actions_later"), len(NEEDS)) if str(x) in planned]
        from . import reviews as _reviews
        kept = {d.isoformat() for d in (_reviews.parse(x) for x in as_list(saved.get("reviews"), _reviews.MAX_REVIEWS))
                if d and d <= self.today}  # real dates, never in the future
        s.reviews = sorted(kept)
        began = _reviews.parse(saved.get("started"))
        if began and began <= self.today:
            s.started = began.isoformat()   # keep the original start day, never a future one
        s.log(f"restored a saved plan; left out: {skipped or 'nothing'}")
        return skipped

    def refresh_card(self) -> None:
        """Rebuild an approved card from the same approved details, with the latest plan."""
        if getattr(self, "card", None) and self.state.card_path:
            write_card(self.card, self.state, self.dir, self.today, self.out_dir)

    def tasks_csv(self) -> str:
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Task", "Date", "Time", "Who is helping", "Status"])
        for t in self.state.helper_tasks:
            w.writerow([safe_cell(t["task"]), safe_cell(t["date"]), safe_cell(t["time"]), safe_cell(t["person"]),
                        TASK_STATUS[task_status(t)]])
        return buf.getvalue()

    # ---- support (T7) --------------------------------------------------------
    def get_support_lines(self) -> dict:
        """Verified support lines to share when someone sounds hopeless or alone.

        Read-only. The agent shares these; it never calls anyone.
        """
        self.state.log("support: shared verified support lines")
        return {
            "lines": [{k: l[k] for k in ("name", "how_to_reach", "hours", "what", "kind")}
                      for l in self.dir.support_lines],
            "emergency": "In an emergency, call 911.",
            "reminder": "Share these gently. The person decides whether to call. Do not push the plan.",
        }

    def review_plan(self) -> dict:
        s = self.state
        return {
            "town": s.town,
            "confirmed_needs": s.confirmed_needs,
            "people_allowed": s.people_allowed(),
            "items": [vars(i) for i in s.items],
            "uncovered": [n for n in s.confirmed_needs if n not in {i.need for i in s.items}],
            "helper_notes": s.helper_notes,
            "helper_tasks": s.helper_tasks,
            "card": s.card_path,
        }

    def _check_need(self, need: str) -> None:
        if need not in NEEDS:
            raise ToolError(f"Unknown need '{need}'. Use one of: {list(NEEDS)}")


# ---------------------------------------------------------------------------
# SDK wrappers
# ---------------------------------------------------------------------------

def _ok(data: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=1)}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"RULE: {msg}"}], "is_error": True}


NEED_ENUM = {"type": "string", "enum": list(NEEDS)}


def build_server(box: Toolbox, trace=print):
    """Create the in-process MCP server. `trace` prints each decision for the demo."""

    async def run(fn, *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            trace(f"  [agent] {box.state.trace[-1]}" if box.state.trace else "")
            return _ok(result)
        except ToolError as e:
            trace(f"  [rule]  blocked: {e}")
            return _err(str(e))

    @tool("confirm_helping_someone",
          "CHECKPOINT. Call first if the user is planning for someone else (a parent, relative, or "
          "friend). The user is asked whether that person knows about the plan and agrees. Do not "
          "plan until this returns agreed=true.",
          {"type": "object", "properties": {
              "person_name": {"type": "string", "description": "their first name, only if the user said it"}},
           "required": []})
    async def confirm_helping_someone(args):
        return await run(box.confirm_helping_someone, args.get("person_name", ""), bool(args.get("agreed")))

    @tool("confirm_needs",
          "CHECKPOINT. Ask the user to confirm the list of needs to plan for. Call before planning.",
          {"type": "object", "properties": {"needs": {"type": "array", "items": NEED_ENUM}},
           "required": ["needs"]})
    async def confirm_needs(args):
        return await run(box.confirm_needs, args["needs"])

    @tool("set_comfort",
          "Record whether the user is comfortable asking a friend for one small thing, and whether "
          "she worries about being a burden, and whether she has no one to ask. Call as soon as she "
          "says any of these.",
          {"type": "object", "properties": {
              "comfortable_asking_people": {"type": "boolean"},
              "worried_about_burden": {"type": "boolean"},
              "no_one_to_ask": {"type": "boolean", "description": "true if the user says they have no one to ask"}},
           "required": ["comfortable_asking_people", "worried_about_burden"]})
    async def set_comfort(args):
        return await run(box.set_comfort, args["comfortable_asking_people"], args["worried_about_burden"],
                         bool(args.get("no_one_to_ask", False)))

    @tool("find_services", "Search the verified local directory for services that meet a need.",
          {"type": "object", "properties": {"need": NEED_ENUM}, "required": ["need"]})
    async def find_services(args):
        return await run(box.find_services, args["need"])

    @tool("find_preparations", "List simple preparations that reduce a need without asking anyone.",
          {"type": "object", "properties": {"need": NEED_ENUM}, "required": ["need"]})
    async def find_preparations(args):
        return await run(box.find_preparations, args["need"])

    @tool("add_to_plan",
          "Record how one need is met. Follow the ladder: service, then preparation, then person "
          "(only if allowed), else blank.",
          {"type": "object", "properties": {
              "need": NEED_ENUM,
              "route": {"type": "string", "enum": list(ROUTES)},
              "ref_id": {"type": "string", "description": "service or preparation id from a search"}},
           "required": ["need", "route"]})
    async def add_to_plan(args):
        return await run(box.add_to_plan, args["need"], args["route"], args.get("ref_id"))

    @tool("draft_helper_note",
          "CHECKPOINT. Draft ONE warm note inviting people the user chose to take one small role. "
          "The user reviews it. It is never sent by you.",
          {"type": "object", "properties": {
              "helper_names": {"type": "array", "items": {"type": "string"}},
              "role": {"type": "string"},
              "note_text": {"type": "string"}},
           "required": ["helper_names", "role", "note_text"]})
    async def draft_helper_note(args):
        return await run(box.draft_helper_note, args["helper_names"], args["role"], args["note_text"])

    @tool("add_helper_task",
          "CHECKPOINT. Add one row to the user's helper table: something they would like help with, "
          "plus a date and time if they gave them. Leave person blank so family can sign up, or use "
          "a name only if the user named that person for this task.",
          {"type": "object", "properties": {
              "task": {"type": "string"}, "person": {"type": "string"},
              "date": {"type": "string"}, "time": {"type": "string"}},
           "required": ["task"]})
    async def add_helper_task(args):
        return await run(box.add_helper_task, args["task"], args.get("person", ""),
                         args.get("date", ""), args.get("time", ""))

    @tool("choose_card_sections",
          "CHECKPOINT. Before asking anything about the fridge card, show the user a checklist of "
          "what can go on it. The user ticks what they want. Then ask only about those.",
          {"type": "object", "properties": {
              "sections": {"type": "array", "items": {"type": "string", "enum": list(CARD_SECTIONS)}}},
           "required": []})
    async def choose_card_sections(args):
        return await run(box.choose_card_sections, args.get("sections", []))

    @tool("make_fridge_card",
          "CHECKPOINT. Create the printable card. Use ONLY details the user typed. Never add medical content.",
          {"type": "object", "properties": {
              "name": {"type": "string"},
              "contacts": {"type": "array", "items": {"type": "object", "properties": {
                  "name": {"type": "string"}, "relationship": {"type": "string"},
                  "phone": {"type": "string"}}, "required": ["name", "relationship", "phone"]}},
              "medications": {"type": "array", "items": {"type": "string"}},
              "allergies": {"type": "array", "items": {"type": "string"}},
              "pet_name": {"type": "string"},
              "pet_caregiver": {"type": "string"},
              "papers": {"type": "string", "description": "only the place the user keeps important papers"}},
           "required": ["name", "contacts", "medications"]})
    async def make_fridge_card(args):
        return await run(box.make_fridge_card, args["name"], args["contacts"], args["medications"],
                         args.get("pet_name", ""), args.get("pet_caregiver", ""),
                         args.get("allergies", []), args.get("papers", ""))

    @tool("get_support_lines",
          "Get verified support lines (988, Call2Talk, Mass 211, wellness check). Use when the user "
          "sounds hopeless, alone, or unsafe, or asks who they can talk to.",
          {"type": "object", "properties": {}})
    async def get_support_lines(args):
        return await run(box.get_support_lines)

    @tool("review_plan", "Read the current plan, including uncovered needs.",
          {"type": "object", "properties": {}})
    async def review_plan(args):
        return _ok(box.review_plan())

    tools = [confirm_helping_someone, confirm_needs, set_comfort, find_services, find_preparations,
             add_to_plan, draft_helper_note, add_helper_task, choose_card_sections, make_fridge_card,
             get_support_lines, review_plan]
    server = create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=tools)
    names = [PREFIX + t.name for t in tools]
    return server, names
