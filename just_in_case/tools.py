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

from .card import write_card
from .config import MAX_HELPERS, OUTPUT_DIR
from .directory import Directory
from .safeguards import ESCORT_RULE_LINE, FALLBACK_LINE, check_helper_note
from .state import NEEDS, ROUTES, PlanItem, PlanState

SERVER_NAME = "jic"
PREFIX = f"mcp__{SERVER_NAME}__"

# Tools that pause for the user's approval (the three human checkpoints).
GATED_TOOLS = {"confirm_needs", "draft_helper_note", "make_fridge_card"}


class ToolError(Exception):
    """Raised for a rule violation. The message goes back to the model."""


class Toolbox:
    def __init__(self, state: PlanState, directory: Directory, out_dir: Path = OUTPUT_DIR,
                 today: date | None = None):
        self.state = state
        self.dir = directory
        self.out_dir = out_dir
        self.today = today or date.today()

    # ---- checkpoint 1 -----------------------------------------------------
    def confirm_needs(self, needs: list[str]) -> dict:
        unknown = [n for n in needs if n not in NEEDS]
        if unknown:
            raise ToolError(f"Unknown needs {unknown}. Use only: {list(NEEDS)}")
        self.state.confirmed_needs = list(dict.fromkeys(needs))
        self.state.log(f"checkpoint 1: user confirmed needs {self.state.confirmed_needs}")
        return {"confirmed_needs": self.state.confirmed_needs}

    def set_comfort(self, comfortable_asking_people: bool, worried_about_burden: bool) -> dict:
        s = self.state
        s.comfortable_asking_people = comfortable_asking_people
        # Once she says she worries about being a burden, it sticks for the session.
        s.worried_about_burden = s.worried_about_burden or worried_about_burden
        s.log(f"comfort: asking_people={s.comfortable_asking_people} burden={s.worried_about_burden}")
        return {"people_allowed": s.people_allowed(), "burden_mode": s.worried_about_burden}

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
            label = "A person she chooses (see note)"
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
    def draft_helper_note(self, helper_names: list[str], role: str, note_text: str) -> dict:
        s = self.state
        if not s.people_allowed():
            raise ToolError("Do not draft a note. People are not part of this plan.")
        if not helper_names:
            raise ToolError("Name at least one person the user chose.")
        if s.helper_count() + len(helper_names) > MAX_HELPERS:
            raise ToolError(f"Keep the plan small: at most {MAX_HELPERS} people in total.")
        problem = check_helper_note(note_text)
        if problem:
            raise ToolError(problem)
        s.helper_notes.append({"helpers": helper_names, "role": role, "text": note_text})
        s.log(f"checkpoint 2: note approved for {helper_names}")
        return {"status": "draft saved. NOT sent. The user sends it herself, if she wants to.",
                "note_text": note_text}

    # ---- checkpoint 3 -------------------------------------------------------
    def make_fridge_card(self, name: str, contacts: list[dict], medications: list[str],
                         pet_name: str = "", pet_caregiver: str = "") -> dict:
        card = {"name": name, "contacts": contacts, "medications": medications,
                "pet": {"name": pet_name, "caregiver": pet_caregiver} if pet_name else None}
        path = write_card(card, self.state, self.dir, self.today, self.out_dir)
        self.state.card_path = str(path)
        self.state.log(f"checkpoint 3: card approved -> {path.name}")
        return {"status": "card created", "file": str(path),
                "print_tip": "Open the file and print it. It works without Wi-Fi after printing."}

    def review_plan(self) -> dict:
        s = self.state
        return {
            "town": s.town,
            "confirmed_needs": s.confirmed_needs,
            "people_allowed": s.people_allowed(),
            "items": [vars(i) for i in s.items],
            "uncovered": [n for n in s.confirmed_needs if n not in {i.need for i in s.items}],
            "helper_notes": s.helper_notes,
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

    @tool("confirm_needs",
          "CHECKPOINT. Ask the user to confirm the list of needs to plan for. Call before planning.",
          {"type": "object", "properties": {"needs": {"type": "array", "items": NEED_ENUM}},
           "required": ["needs"]})
    async def confirm_needs(args):
        return await run(box.confirm_needs, args["needs"])

    @tool("set_comfort",
          "Record whether the user is comfortable asking a friend for one small thing, and whether "
          "she worries about being a burden. Call as soon as she says either.",
          {"type": "object", "properties": {
              "comfortable_asking_people": {"type": "boolean"},
              "worried_about_burden": {"type": "boolean"}},
           "required": ["comfortable_asking_people", "worried_about_burden"]})
    async def set_comfort(args):
        return await run(box.set_comfort, args["comfortable_asking_people"], args["worried_about_burden"])

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

    @tool("make_fridge_card",
          "CHECKPOINT. Create the printable card. Use ONLY details the user typed. Never add medical content.",
          {"type": "object", "properties": {
              "name": {"type": "string"},
              "contacts": {"type": "array", "items": {"type": "object", "properties": {
                  "name": {"type": "string"}, "relationship": {"type": "string"},
                  "phone": {"type": "string"}}, "required": ["name", "relationship", "phone"]}},
              "medications": {"type": "array", "items": {"type": "string"}},
              "pet_name": {"type": "string"},
              "pet_caregiver": {"type": "string"}},
           "required": ["name", "contacts", "medications"]})
    async def make_fridge_card(args):
        return await run(box.make_fridge_card, args["name"], args["contacts"], args["medications"],
                         args.get("pet_name", ""), args.get("pet_caregiver", ""))

    @tool("review_plan", "Read the current plan, including uncovered needs.",
          {"type": "object", "properties": {}})
    async def review_plan(args):
        return _ok(box.review_plan())

    tools = [confirm_needs, set_comfort, find_services, find_preparations,
             add_to_plan, draft_helper_note, make_fridge_card, review_plan]
    server = create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=tools)
    names = [PREFIX + t.name for t in tools]
    return server, names
