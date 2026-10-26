"""The Just in Case website.

    python serve.py           # real agent (uses your OpenRouter key)
    python serve.py --demo    # scripted demo, no AI, no cost

How it fits together:
  browser  <-- WebSocket -->  this server  -->  JustInCaseAgent  -->  OpenRouter
The API key stays here on the server. The browser only ever sees the agent's
words, the plan, and the approval requests.

Messages the browser sends:
  {"type": "say", "text": "..."}                 something the user typed
  {"type": "answer", "id": "...", "ok": true}    Yes / Change on a checkpoint
Messages this server sends:
  hello, agent, activity, approval, plan, busy, error
"""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
import html as htmlmod

from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from just_in_case.config import DATA_DIR, OUTPUT_DIR
from just_in_case.config import AGENT_NAME, AI_DAILY_LIMIT, REPORT_EMAIL, has_ai_key
from just_in_case.daily_limit import DailyLimit
from just_in_case.prompts import GREETING
from just_in_case.why import why_this
from just_in_case import attention, replies
from just_in_case.card import plan_email_text
from just_in_case.invites import (FOLLOW_UP_SUBJECT, HOME_SAFE, INVITE_SUBJECT, LINK_LINE, PEOPLE_REMINDER, PEOPLE_TEMPLATE, REMINDER_SUBJECT,
                                  THANKS_SUBJECT, YOU_SEND_LINE, drafts_for)
from just_in_case import timeline
from just_in_case.encouragement import encouragement
from just_in_case.next_step import pick_next_step
from just_in_case.reviews import review_status
from just_in_case.state import CARD_SECTIONS, NEED_WHY, NEEDS
from web.share_store import ShareError, ShareStore

STATIC = Path(__file__).parent / "static"
ROOT = Path(__file__).resolve().parent.parent


def code_fingerprint() -> str:
    """A short hash of the code on disk. If it changes while the server runs,
    someone ran git pull without restarting, so the page shows a warning."""
    import hashlib
    h = hashlib.sha256()
    for folder in ("just_in_case", "web"):
        for f in sorted((ROOT / folder).rglob("*")):
            if f.suffix in (".py", ".js", ".html", ".css") and "__pycache__" not in f.parts:
                h.update(f.read_bytes())
    return h.hexdigest()[:12]


STARTED_WITH = code_fingerprint()
DEMO = os.getenv("JIC_DEMO") == "1"

app = FastAPI(title="Just in Case")
AI_LIMIT = DailyLimit(AI_DAILY_LIMIT)   # AI replies per day, all visitors together
LIMIT_LINE = (f"{AGENT_NAME} has reached today's limit for AI replies, so the guided planner will take it "
              "from here. It uses the same checked services and rules, and your plan is kept. "
              "What would you like to work on next?")
SESSIONS: dict[str, "Session"] = {}
SHARES = ShareStore(OUTPUT_DIR / "shared")
SHARE_OWNERS: dict[str, "Session"] = {}  # share id -> the owner's open visit
SHARES.purge_expired()


# ---------------------------------------------------------------------------
# Plain-language descriptions for the activity feed
# ---------------------------------------------------------------------------

# Plan B: when a friend is giving the ride home, keep the best matching service ready.
PLAN_B_NEEDS = {"ride_home_after_procedure"}


def plan_b(item, state, directory) -> dict | None:
    if item.need not in PLAN_B_NEEDS or item.route != "person":
        return None
    found = directory.find_services(item.need, state.town)   # free first, from the checked list only
    if not found:
        return None
    s = directory.by_id[found[0]["id"]]
    return {"name": s["name"], "phone": s.get("phone", ""), "url": s.get("url", ""), "cost": s["cost"]}


def need_label(key: str) -> str:
    return NEEDS.get(key, key.replace("_", " "))


def describe_tool(short: str, data: dict) -> tuple[str, str] | None:
    """(kind, text) shown in the feed when the agent uses a tool."""
    need = need_label(data.get("need", "")) if data.get("need") else ""
    return {
        "confirm_helping_someone": ("ask", "Checking that the person you're helping agrees"),
        "confirm_needs": ("ask", "Checking the list of things to plan for with you"),
        "set_comfort": ("note", "Noting who you feel comfortable asking"),
        "find_services": ("search", f"Looking for services: {need.lower()}"),
        "find_preparations": ("search", f"Looking for simple ways to get ready: {need.lower()}"),
        "add_to_plan": ("add", f"Adding to your plan: {need.lower()}"),
        "draft_helper_note": ("write", "Writing a note for you to look over"),
        "add_helper_task": ("add", "Adding a row to your helper table"),
        "choose_card_sections": ("ask", "Asking what you'd like on your fridge card"),
        "make_fridge_card": ("card", "Making your fridge card"),
        "get_support_lines": ("heart", "Finding people you can talk to"),
        "review_plan": ("search", "Checking your plan"),
    }.get(short)


def describe_trace(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if line.startswith("[rule]"):
        detail = line.split(":", 1)[-1].strip()
        return ("rule", f"A safety rule stepped in. {detail}")
    if line.startswith("[human]"):
        return ("human", "You asked for a change")
    return None


# ---------------------------------------------------------------------------
# One browser tab = one session
# ---------------------------------------------------------------------------

class Session:
    summary_ok = False  # nothing is made until the person says yes

    def __init__(self, ws: WebSocket, mode: str = "live"):
        self.id = uuid.uuid4().hex
        self.ws = ws
        self.mode = mode              # "live" (the AI agent), "guided" (no AI), or "example" (Ruth's story, no AI)
        self.demo = mode != "live"     # no AI: the same tools and approval gate, run by the app's own rules
        self.out: asyncio.Queue = asyncio.Queue()
        self.pending: dict[str, asyncio.Future] = {}
        self.agent = None
        self.lock = asyncio.Lock()  # one user message at a time
        # Each visit gets its own folder for the card, deleted when the visit ends.
        self.out_dir = OUTPUT_DIR / "sessions" / self.id
        self.share_id: str | None = None
        self.owner_token: str | None = None
        self.share_expires: float | None = None
        self.recapped: tuple = ()  # the plan as of the last "So far" recap
        self.returning = False  # restored a saved plan; tell the live agent once
        self.ready_said = False  # "Your plan is ready" is said once per visit
        self.summary_ok = False  # the person agreed to make a readiness summary this visit
        self.declined: list[str] = []  # parts the user said "No thanks" to, for the agent's next turn

    def emit(self, msg: dict) -> None:
        self.out.put_nowait(msg)

    # -- callbacks handed to the agent --------------------------------------
    def on_tool(self, short: str, data: dict) -> None:
        d = describe_tool(short, data)
        if d:
            self.emit({"type": "activity", "kind": d[0], "text": d[1]})

    def on_trace(self, line: str) -> None:
        if not line:
            return
        d = describe_trace(line)
        if d:
            self.emit({"type": "activity", "kind": d[0], "text": d[1]})
        self.sync_share()
        self.emit({"type": "plan", "plan": self.plan()})

    async def approver(self, short: str, data: dict):
        """Checkpoint: show Yes / Change buttons and wait for the user."""
        aid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.pending[aid] = fut
        self.emit({"type": "activity", "kind": "wait", "text": "Waiting for your OK"})
        self.emit({"type": "approval", "id": aid, "kind": short, "data": self.approval_view(short, data)})
        try:
            return await fut
        finally:
            self.pending.pop(aid, None)

    def approval_view(self, short: str, data: dict) -> dict:
        if short == "confirm_needs":
            return {"needs": [{"key": n, "label": need_label(n)} for n in data.get("needs", [])]}
        if short == "draft_helper_note":
            return {"to": data.get("helper_names", []), "role": data.get("role", ""),
                    "text": data.get("note_text", "")}
        if short == "choose_card_sections":
            # Every box starts unticked. The agent's suggestion is not shown.
            return {"options": [{"key": k, "label": v} for k, v in CARD_SECTIONS.items()]}
        if short == "add_helper_task":
            return {k: data.get(k, "") for k in ("task", "person", "date", "time")}
        if short == "confirm_helping_someone":
            return {"name": data.get("person_name", "")}
        if short == "make_fridge_card":
            return {"name": data.get("name", ""), "contacts": data.get("contacts", []),
                    "medications": data.get("medications", []), "allergies": data.get("allergies", []),
                    "pet": data.get("pet_name", ""), "pet_caregiver": data.get("pet_caregiver", ""),
                    "papers": data.get("papers", "")}
        return data

    def answer(self, aid: str, ok: bool, choice=None) -> None:
        fut = self.pending.get(aid)
        if fut and not fut.done():
            if isinstance(choice, list):
                fut.set_result([str(c) for c in choice if str(c) in CARD_SECTIONS])
            else:
                fut.set_result(bool(ok))

    # -- plan snapshot for the fridge panel ---------------------------------
    def plan(self) -> dict:
        if not self.agent:
            return {"items": [], "notes": [], "card": None}
        state, d = self.agent.state, self.agent.box.dir
        helpers = [h for n in state.helper_notes for h in n["helpers"]]
        items = []
        for i in state.items:
            ref = d.by_id.get(i.ref_id or "", {})
            name = ref.get("name", i.label)
            if i.route == "person":
                name = " and ".join(helpers) if helpers else "Someone you chose"
            items.append({
                "need": i.need, "need_label": need_label(i.need), "route": i.route,
                "name": name, "phone": ref.get("phone", ""),
                "url": ref.get("url", ""), "what": ref.get("what") or ref.get("how_to_use", ""),
                "flags": i.flags, "why": NEED_WHY.get(i.need, ""), "user_ok": i.user_ok,
                "why_this": why_this(i, state, d),   # one plain sentence, from facts only
                "plan_b": plan_b(i, state, d),
                "checked": ref.get("last_verified", "") if i.route == "service" else "",
            })
        covered = {i.need for i in state.items if i.route != "blank" and i.user_ok}
        return {
            "items": items,
            # No one to ask (or would rather not): show where to find more help, not an empty table.
            "services_only": state.services_only(),
            "help_lines": [{"name": l["name"], "how_to_reach": l["how_to_reach"]}
                           for l in d.support_lines if l["id"] in ("line_age_strong", "line_mass211")],
            "next_step": pick_next_step(items, state.actions_done, state.actions_later),
            "review": review_status(state.reviews, self.agent.box.today, any(i["user_ok"] for i in items)),
            "encouragement": encouragement(len(covered & set(state.confirmed_needs)), len(state.confirmed_needs),
                                           bool(state.card_path)),
            "progress": {"covered": len(covered & set(state.confirmed_needs)),
                         "total": len(state.confirmed_needs)},
            "ready": self.is_ready(),
            "timeline": timeline.build(state, self.agent.box.today),
            "night_before": timeline.night_before(state, d, self.agent.box.today),
            "night_print": f"/night/{self.id}",
            "notes": state.helper_notes,
            "tasks": state.helper_tasks,
            # The agent's invite and reminder drafts for each task (fixed wording, checked). The person sends them.
            "invites": drafts_for(state.helper_tasks, self.agent.box.today),
            # Said they don't want to be a burden: no nudges to invite anyone. The buttons stay, quietly.
            "quiet_invites": state.worried_about_burden,
            "invite_words": {"link_line": LINK_LINE, "you_send": YOU_SEND_LINE,
                             "invite_subject": INVITE_SUBJECT, "reminder_subject": REMINDER_SUBJECT,
                             "people_reminder": PEOPLE_REMINDER, "thanks_subject": THANKS_SUBJECT, "home_safe": HOME_SAFE,
                             "follow_up_subject": FOLLOW_UP_SUBJECT,
                             "no_link": "Without your list link, Just in Case can't see their answer."},
            # What's new: helpers' replies on the shared list, gathered by code each time the plan is shown.
            "whats_new": replies.whats_new(state, d, self.agent.box.today, bool(self.share_id)),
            "card": f"/card/{self.id}" if state.card_path else None,
            "wallet": f"/wallet/{self.id}" if state.card_path else None,
            "report_email": REPORT_EMAIL,
            # What needs attention: real gaps only, checked by code each time the plan is shown.
            "attention": attention.check(state, d, self.agent.box.today) if state.confirmed_needs else None,
            "all_set": attention.ALL_SET,
            # "Email my plan": plain text for the person's own email app. No medications or allergies.
            "plan_email": plan_email_text(state, d) if any(i.user_ok for i in state.items) else None,
            "plan_print": f"/plan/{self.id}" if state.items else None,
            "summary_available": any(i["user_ok"] for i in items),          # the button may show
            "summary_print": f"/summary/{self.id}" if self.summary_ok and any(i["user_ok"] for i in items) else None,
            "table_csv": f"/table/{self.id}.csv",
            "table_print": f"/table/{self.id}",
            "share": {"url": f"/share/{self.share_id}",
                      "owner_url": f"/share/{self.share_id}?owner={self.owner_token}",
                      "expires": self.share_expires} if self.share_id else None,
        }

    # -- "So far, we've planned..." (built from the plan, never by the model) -
    def recap(self) -> str | None:
        plan = self.plan()
        if not plan["items"]:
            return None
        lines = [f"- {i['need_label']}: {'not covered yet' if i['route'] == 'blank' else i['name']}"
                 + ("" if i["user_ok"] or i["route"] == "blank" else " (waiting for your OK)")
                 for i in plan["items"]]
        planned = {i["need"] for i in plan["items"]}
        left = [need_label(n).lower() for n in self.agent.state.confirmed_needs if n not in planned]
        text = "So far, we've planned:\n\n" + "\n".join(lines)
        if left:
            text += "\n\nStill to plan: " + "; ".join(left) + "."
        return text

    def maybe_recap(self) -> None:
        """Show a recap once each time the plan changes, before the agent's next words."""
        if not self.agent:
            return
        key = tuple((i.need, i.route, i.ref_id) for i in self.agent.state.items)
        if key != self.recapped:
            self.recapped = key
            text = self.recap()
            if text:
                self.emit({"type": "recap", "text": text})
        if self.is_ready() and not self.ready_said:
            self.ready_said = True
            st = self.agent.state
            next_step = ("You can print your plan or your fridge card any time." if st.card_path else
                         "You can print your plan any time. When you'd like, we can make your fridge card too.")
            self.emit({"type": "ready", "text": f"Your plan is ready. {next_step}"})

    def is_ready(self) -> bool:
        st = self.agent.state
        covered = {i.need for i in st.items if i.route != "blank" and i.user_ok}
        return bool(st.confirmed_needs) and set(st.confirmed_needs) <= covered

    # -- "Yes, add this" / "No thanks" on each plan item ------------------------
    def review_edit(self, action: str) -> None:
        from just_in_case.tools import ToolError
        try:
            self.agent.box.user_review(action)
        except ToolError as e:
            self.emit({"type": "table_error", "text": str(e)})
        self.emit({"type": "plan", "plan": self.plan()})

    def next_step_edit(self, need: str, action: str) -> None:
        from just_in_case.tools import ToolError
        try:
            self.agent.box.user_next_step(need, action)
        except ToolError as e:
            self.emit({"type": "table_error", "text": str(e)})
        self.emit({"type": "plan", "plan": self.plan()})

    def answer_item(self, need: str, ok: bool) -> None:
        from just_in_case.tools import ToolError
        try:
            self.agent.box.user_confirm_item(need, ok)
        except ToolError as e:
            self.emit({"type": "table_error", "text": str(e)})
            return
        self.emit({"type": "activity", "kind": "human",
                   "text": f"You {'added' if ok else 'said no thanks to'}: {need_label(need).lower()}"})
        if not ok:
            self.declined.append(need_label(need).lower())
        self.maybe_recap()
        self.sync_share()
        self.emit({"type": "plan", "plan": self.plan()})

    # -- "Getting ready" timeline: the user's date and ticks -------------------
    def timeline_edit(self, msg: dict) -> None:
        from just_in_case.tools import ToolError
        try:
            if msg.get("type") == "event_date":
                self.agent.box.user_set_event_date(str(msg.get("date", ""))[:10])
            else:
                self.agent.box.user_tick_step(str(msg.get("id", ""))[:80], msg.get("done") is True)
        except ToolError as e:
            self.emit({"type": "timeline_error", "text": str(e)})
        self.emit({"type": "plan", "plan": self.plan()})

    # -- save and continue later (kept in the user's own browser) -------------
    def save(self) -> None:
        if self.demo and self.ws.query_params.get("example") == "1":
            return  # Ruth's example is not saved
        self.emit({"type": "saved", "state": self.agent.box.export()})

    def restore(self, saved) -> None:
        st = self.agent.state
        if st.user_said or st.items or not isinstance(saved, dict):
            self.emit({"type": "table_error", "text": "A saved plan can only be opened at the start of a visit."})
            return
        skipped = self.agent.box.restore(saved)
        if hasattr(self.agent, "turn") and st.items:
            self.agent.turn = 3  # the scripted demo has no more steps for a restored plan
        self.returning = True
        self.recapped = ()
        self.emit({"type": "restored", "skipped": skipped})
        self.maybe_recap()
        self.sync_share()
        self.emit({"type": "plan", "plan": self.plan()})

    # -- sharing (only with consent) -----------------------------------------
    def summary_consent(self, agree) -> None:
        """Making the summary is opt in. Without a yes, the page address answers 403."""
        self.summary_ok = agree is True
        if self.summary_ok:
            self.emit({"type": "activity", "kind": "human", "text": "You chose to make your readiness summary. It is not saved or sent."})
        self.emit({"type": "plan", "plan": self.plan()})

    def share_create(self, consent: bool, label: str) -> None:
        if consent is not True:
            self.emit({"type": "table_error", "text": "Nothing was shared. Sharing needs your OK first."})
            return
        tasks = self.agent.state.helper_tasks
        if not tasks:
            self.emit({"type": "table_error", "text": "Add at least one task before sharing."})
            return
        rec, token = SHARES.create(label, tasks)
        self.share_id, self.owner_token, self.share_expires = rec["id"], token, rec["expires"]
        SHARE_OWNERS[rec["id"]] = self
        self.emit({"type": "activity", "kind": "human",
                   "text": "You saved and shared your helper list. Only people with the link can see it."})
        self.emit({"type": "plan", "plan": self.plan()})

    def share_delete(self) -> None:
        if self.share_id:
            try:
                SHARES.delete(self.share_id, self.owner_token)
            except ShareError:
                pass
            SHARE_OWNERS.pop(self.share_id, None)
        self.share_id = self.owner_token = self.share_expires = None
        self.emit({"type": "activity", "kind": "human", "text": "You stopped sharing. Your list was deleted."})
        self.emit({"type": "plan", "plan": self.plan()})

    def sync_share(self) -> None:
        """Push the owner's latest table to the shared copy."""
        if not self.share_id:
            return
        try:
            SHARES.update_tasks(self.share_id, self.agent.state.helper_tasks)
        except ShareError:  # deleted from the owner link, or expired
            SHARE_OWNERS.pop(self.share_id, None)
            self.share_id = self.owner_token = self.share_expires = None

    def apply_share(self, rec: dict, activity: str) -> None:
        """A helper signed up or stepped back: update the owner's table live."""
        people = {t["id"]: t["person"] for t in rec["tasks"]}
        offers = {t["id"]: t.get("offers", []) for t in rec["tasks"]}
        for t in self.agent.state.helper_tasks:
            if t["id"] in people:
                if people[t["id"]] != t["person"]:
                    t["confirmed"] = False  # a new name, or none: not confirmed yet
                    if people[t["id"]]:
                        t["invited"] = False  # the helper signed up: Invited becomes Someone's helping
                t["person"] = people[t["id"]]
                # Each offer, with the days and times that work. Never the helper's token.
                t["offers"] = [{k: o[k] for k in ("id", "name", "days", "times", "note")} for o in offers[t["id"]]]
        self.agent.box.refresh_card()
        self.emit({"type": "activity", "kind": "heart", "text": activity})
        self.emit({"type": "plan", "plan": self.plan()})

    def choose_helper(self, task_id, offer_id) -> None:
        from just_in_case.tools import ToolError
        try:
            drafts = self.agent.box.user_choose_helper(task_id, offer_id)
        except ToolError as e:
            self.emit({"type": "table_error", "text": str(e)})
            return
        t = next(t for t in self.agent.state.helper_tasks if t["id"] == task_id)
        self.emit({"type": "activity", "kind": "human", "text": f"You chose {t['person'].split(' ')[0]} for: {t['task']}"})
        self.sync_share()
        if drafts:
            self.emit({"type": "thank_you", "task": t["task"], "drafts": drafts})
        self.emit({"type": "plan", "plan": self.plan()})

    def share_reconnect(self, share_id: str, owner: str) -> None:
        """A saved plan reopened: look at its shared list again for new sign-ups (owner link only)."""
        if self.share_id:
            return
        try:
            rec = SHARES.get(share_id)
        except ShareError:
            return
        if not SHARES.is_owner(rec, owner):
            return
        tasks, used = self.agent.state.helper_tasks, set()
        for t in tasks:   # line the plan's rows up with the shared rows, by task
            row = next((r for r in rec["tasks"] if r["id"] not in used and r["task"] == t["task"][:120]), None)
            if row is None:
                return    # the lists no longer match: leave it, the person can share again
            used.add(row["id"]); t["id"] = row["id"]
        if tasks:
            self.agent.state.next_task_id = max(t["id"] for t in tasks) + 1
        self.share_id, self.owner_token, self.share_expires = rec["id"], owner, rec["expires"]
        SHARE_OWNERS[rec["id"]] = self
        self.apply_share(rec, "Checked your shared helper list for new sign-ups")

    def example_replies(self) -> None:
        """Ruth's example only: Doris answers on Ruth's shared list, through the same sign-up code."""
        if self.mode != "example" or not self.share_id:
            return
        today = self.agent.box.today
        for name, words, days, times, note in (("Doris", "fifi", "any", "mornings", "Happy to feed her."),):
            row = next((t for t in self.agent.state.helper_tasks if words in t["task"].lower() and not t.get("person")), None)
            if row:
                try:
                    rec, _ = SHARES.claim(self.share_id, row["id"], name, days, times, note, today=today)
                except ShareError:
                    continue
                r = next(x for x in rec["tasks"] if x["id"] == row["id"])
                self.apply_share(rec, f"{name} signed up to help: {r['task']}" if r["person"] else f"{name} offered to help: {r['task']}")

    def plans_changed(self, new_date: str, new_time: str) -> None:
        from just_in_case.tools import ToolError
        try:
            out = self.agent.box.user_plans_changed(new_date, new_time)
        except ToolError as e:
            self.emit({"type": "timeline_error", "text": str(e)})
            return
        self.emit({"type": "activity", "kind": "human", "text": f"You changed your plans to {out['when']}"})
        self.sync_share()
        self.emit({"type": "plans_changed", **out})
        self.emit({"type": "plan", "plan": self.plan()})

    def edit_table(self, action: str, row: dict) -> None:
        from just_in_case.tools import ToolError
        try:
            self.agent.box.user_edit_task(action, row)
            self.emit({"type": "activity", "kind": "human", "text": {
                "add": "You added a row to your helper table",
                "update": "You changed a row in your helper table",
                "delete": "You removed a row from your helper table",
                "confirm": "You updated whether a helper is confirmed",
                "invited": "You sent an invite from your own phone or email",
                "followed_up": "You sent a follow-up from your own phone or email",
                "reply": "You noted how your helper replied",
                "declined_ok": "You noted that",
                "reminded": "You sent a reminder from your own phone or email"}.get(action, "You edited your table")})
        except ToolError as e:
            self.emit({"type": "table_error", "text": str(e)})
        self.sync_share()
        self.emit({"type": "plan", "plan": self.plan()})

    # -- lifecycle -----------------------------------------------------------
    async def start(self):
        if self.demo:
            from web.demo_agent import DemoAgent
            self.agent = DemoAgent(self.approver, trace=self.on_trace, on_tool=self.on_tool,
                                   out_dir=self.out_dir, mode=self.mode)
        else:
            from just_in_case.agent import JustInCaseAgent
            self.agent = JustInCaseAgent(approver=self.approver, trace=self.on_trace,
                                         on_tool=self.on_tool, out_dir=self.out_dir)
        await self.agent.__aenter__()

    async def stop(self):
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result(False)
        if self.agent:
            try:
                await self.agent.__aexit__(None, None, None)
            except Exception:
                pass
        shutil.rmtree(self.out_dir, ignore_errors=True)

    async def switch_to_guided(self) -> None:
        """Today's AI limit is reached: carry the plan over to the guided planner (no AI)."""
        from web.demo_agent import DemoAgent
        saved = self.agent.box.export()
        ids = [t["id"] for t in self.agent.state.helper_tasks]
        try:
            await self.agent.__aexit__(None, None, None)
        except Exception:
            pass
        self.mode, self.demo = "guided", True
        self.agent = DemoAgent(self.approver, trace=self.on_trace, on_tool=self.on_tool,
                               out_dir=self.out_dir, mode="guided")
        await self.agent.__aenter__()
        self.agent.box.restore(saved)
        st = self.agent.state
        if len(st.helper_tasks) == len(ids):   # same task numbers, so shared-list links still match
            for t, i in zip(st.helper_tasks, ids):
                t["id"] = i
            st.next_task_id = max(ids, default=0) + 1
        if hasattr(self.agent, "turn") and st.items:
            self.agent.turn = 3
        self.emit({"type": "mode", "mode": "guided"})

    async def handle_say(self, text: str):
        async with self.lock:
            self.emit({"type": "busy", "on": True})
            if self.mode == "live" and not AI_LIMIT.take():
                await self.switch_to_guided()
                self.agent.state.user_said.append(text)
                self.emit({"type": "agent", "text": LIMIT_LINE})
                self.emit({"type": "plan", "plan": self.plan()})
                self.emit({"type": "busy", "on": False})
                return
            def on_text(t: str) -> None:
                self.maybe_recap()
                self.emit({"type": "agent", "text": t.removeprefix(f"\n{AGENT_NAME}: ")})
            notes = []
            if self.returning:
                self.returning = False
                notes.append("the user is returning to a plan they saved earlier. Call review_plan first, "
                             "then continue from there.")
            if self.declined:
                notes.append("on the page, the user said no thanks to: " + ", ".join(self.declined)
                             + ". Those parts are off the plan. Ask what they would prefer, if anything.")
                self.declined = []
            context = f"[Note from the app, not the user: {' '.join(notes)}]" if notes else ""
            try:
                await self.agent.send(text, on_text=on_text, context=context)
                tip = getattr(self.agent, "suggestion", None)   # the guided planner's optional outside service
                if tip:
                    self.agent.suggestion = None
                    self.emit({"type": "suggest", **tip})
            except Exception as e:  # keep the page usable, say what happened
                self.emit({"type": "error", "text": f"Something went wrong on our side: {e}. "
                           "Please try sending your message again."})
            finally:
                self.maybe_recap()
                self.emit({"type": "plan", "plan": self.plan()})
                self.emit({"type": "busy", "on": False})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Optional site password (for a hosted copy that uses the AI key)
# ---------------------------------------------------------------------------
# Set SITE_PASSWORD on the host to make every page ask for it first. Leave it
# unset on your own computer, or for the public copy with no AI key.

AUTH_COOKIE = "jic_pass"


def site_password() -> str:
    return os.getenv("SITE_PASSWORD", "").strip()


def _pass_token(password: str) -> str:
    import hashlib
    return hashlib.sha256(f"just-in-case:{password}".encode()).hexdigest()


def signed_in(cookies) -> bool:
    import hmac
    pw = site_password()
    return not pw or hmac.compare_digest(cookies.get(AUTH_COOKIE, ""), _pass_token(pw))


LOGIN_OPEN = ("/login", "/static/style.css", "/favicon.ico")


def login_page(wrong: bool = False) -> str:
    note = '<p class="login-wrong" role="alert">That password did not match. Please try again.</p>' if wrong else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>Sign in</title>
<style>
 body {{ font-family: "Atkinson Hyperlegible", Verdana, Arial, sans-serif; font-size: 18px; background: #F7F4EE; color: #1E2A2E;
        margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px; box-sizing: border-box; }}
 main {{ background: #fff; border-radius: 16px; padding: 28px; max-width: 26rem; width: 100%; box-sizing: border-box;
        box-shadow: 0 2px 10px rgba(0,0,0,.08); }}
 h1 {{ font-size: 1.5rem; margin: 0 0 .5rem; }} p {{ line-height: 1.5; }}
 label {{ display: block; font-weight: 700; margin: 1rem 0 .4rem; }}
 input {{ width: 100%; min-height: 48px; font: inherit; padding: 0 12px; border: 2px solid #5B6B70; border-radius: 10px; box-sizing: border-box; }}
 input:focus, button:focus {{ outline: 3px solid #1F6F78; outline-offset: 2px; }}
 button {{ margin-top: 1rem; min-height: 48px; width: 100%; font: inherit; font-weight: 700; border: 0; border-radius: 10px;
          background: #1F6F78; color: #fff; cursor: pointer; }}
 .login-wrong {{ color: #9B2C1F; font-weight: 700; }}
 .e911 {{ font-weight: 700; }}
</style></head><body><main>
<h1>Just in Case</h1>
<p>This copy is for our team. Please type the password you were given.</p>
{note}
<form method="post" action="/login">
 <label for="pw">Password</label>
 <input id="pw" name="password" type="password" autocomplete="current-password" required autofocus>
 <button type="submit">Sign in</button>
</form>
<p class="e911">In an emergency, call 911.</p>
</main></body></html>"""


@app.middleware("http")
async def require_site_password(request, call_next):
    if site_password() and request.url.path not in LOGIN_OPEN and not signed_in(request.cookies):
        if request.url.path == "/" or request.url.path.startswith(("/share/", "/card/", "/plan/", "/table/", "/summary/")):
            return Response(status_code=303, headers={"Location": "/login"})
        return JSONResponse({"error": "Please sign in first."}, status_code=401)
    return await call_next(request)


@app.get("/login")
async def login_form():
    if not site_password():
        return Response(status_code=303, headers={"Location": "/"})
    return HTMLResponse(login_page())


@app.post("/login")
async def login_submit(request: Request):
    import hmac
    from urllib.parse import parse_qs
    pw = site_password()
    if not pw:
        return Response(status_code=303, headers={"Location": "/"})
    tried = parse_qs((await request.body()).decode("utf-8", "replace")).get("password", [""])[0]
    if not hmac.compare_digest(tried.encode(), pw.encode()):
        await asyncio.sleep(1)  # slows down guessing
        return HTMLResponse(login_page(wrong=True), status_code=401)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp = Response(status_code=303, headers={"Location": "/"})
    resp.set_cookie(AUTH_COOKIE, _pass_token(pw), max_age=30 * 86400, httponly=True,
                    samesite="lax", secure=secure)
    return resp


@app.middleware("http")
async def check_for_updates(request, call_next):
    """Pages and their files are re-checked on each visit (a quick 304 when unchanged),
    so a browser never mixes an old style.css or app.js with a new page."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith(("/static/", "/share/")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def ai_on() -> bool:
    """Whether a new visit here would talk to the AI service (a key, not demo mode, under today's limit)."""
    return not DEMO and has_ai_key() and not AI_LIMIT.reached()


def render_page(name: str, agent_name: str = AGENT_NAME) -> str:
    """A page with the agent's name filled in. The name lives in config.py, nowhere else.
    data-ai says whether this visit talks to the AI service; the privacy wording follows it."""
    return ((STATIC / name).read_text().replace("{{AGENT_NAME}}", htmlmod.escape(agent_name))
            .replace("{{AI_MODE}}", "on" if ai_on() else "off"))


@app.get("/")
async def index():
    return HTMLResponse(render_page("index.html"))


@app.get("/api/config")
async def config():
    return JSONResponse({"demo": DEMO, "ai": not DEMO and has_ai_key(), "greeting": GREETING, "agent_name": AGENT_NAME,
                         "needs_restart": code_fingerprint() != STARTED_WITH})


@app.get("/api/facts")
async def facts():
    """"By the numbers" on the About tab. Counted from the code and data, so it stays true."""
    import re
    from just_in_case.tools import GATED_TOOLS
    services = [s for s in json.loads((DATA_DIR / "services.json").read_text())["services"] if not s.get("sample")]
    lines = json.loads((DATA_DIR / "support_lines.json").read_text())["lines"]
    tests = ROOT / "tests" / "test_safeguards.py"
    return JSONResponse({
        "services": len(services),
        "free_services": sum(s["cost"] == "free" for s in services),
        "support_lines": len(lines),
        "checkpoints": len(GATED_TOOLS),
        "safeguard_tests": len(re.findall(r"^def test_", tests.read_text(), re.M)) if tests.exists() else None,
    })


@app.get("/api/support")
async def support():
    """Verified support lines for the "People you can talk to" box."""
    lines = json.loads((DATA_DIR / "support_lines.json").read_text())["lines"]
    keep = ("name", "how_to_reach", "hours", "what", "kind", "url")
    return JSONResponse([{k: l.get(k, "") for k in keep} for l in lines])


@app.get("/api/family-updates")
async def family_updates():
    """For "Keep family in the loop": outside services for sharing updates, only from the services list."""
    from just_in_case.directory import Directory
    from just_in_case.safeguards import separate_service_line
    d = Directory()
    return JSONResponse([{**{k: s[k] for k in ("name", "url", "cost", "how_to_use")},
                          "last_verified": d.by_id[s["id"]]["last_verified"],
                          "separate": separate_service_line(s["name"])} for s in d.find_services("family_updates", "Boston")])


FEEDBACK_FILE = OUTPUT_DIR / "feedback" / "answers.jsonl"
FEEDBACK_MAX_LINES = 5000


@app.post("/api/feedback")
async def feedback(request: Request):
    """ "Was this helpful?": saves ONLY the yes/no and the comment. No session, plan, name, or time."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Please try again."}, status_code=400)
    helpful = body.get("helpful") if isinstance(body, dict) else None
    if helpful not in (True, False):
        return JSONResponse({"error": "Please pick yes or no."}, status_code=400)
    comment = str(body.get("comment") or "").replace("\r", " ").strip()[:500]
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = FEEDBACK_FILE.read_text().splitlines() if FEEDBACK_FILE.exists() else []
    lines = (lines + [json.dumps({"helpful": helpful, "comment": comment})])[-FEEDBACK_MAX_LINES:]
    FEEDBACK_FILE.write_text("\n".join(lines) + "\n")
    return JSONResponse({"saved": True})


@app.get("/night/{sid}")
async def night_print(sid: str):
    """The night-before summary, large print. Made on request from the plan, never saved."""
    s = SESSIONS.get(sid)
    nb = timeline.night_before(s.agent.state, s.agent.box.dir, s.agent.box.today) if s and s.agent else None
    if not nb:
        return JSONResponse({"error": "No summary yet"}, status_code=404)
    e = htmlmod.escape
    items = "".join(f"<li>{e(x)}</li>" for x in nb["lines"])
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="color-scheme" content="light only">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>The night before</title>
<style> body {{ font-family: "Atkinson Hyperlegible", Verdana, Arial, sans-serif; font-size: 20pt; color: #000; background: #fff; max-width: 8in; margin: .4in auto; padding: 0 16px; }}
 h1 {{ font-size: 28pt; }} li {{ margin: .12in 0; }} .print {{ font: inherit; font-weight: 700; padding: .1in .35in; min-height: 48px; border: 0; border-radius: 10px; background: #1F6F78; color: #fff; }}
 @media print {{ .print {{ display: none; }} }}</style></head><body>
<h1>{e(nb["title"])}</h1><ul>{items}</ul>
<button class="print" onclick="window.print()">Print</button></body></html>""")


@app.get("/wallet/{sid}")
async def wallet(sid: str):
    """The wallet card: only the approved fridge card details. Made on request, never saved."""
    from just_in_case.card import render_wallet_html
    s = SESSIONS.get(sid)
    card = getattr(s.agent.box, "card", None) if s and s.agent else None
    if not card or not s.agent.state.card_path:
        return JSONResponse({"error": "No card yet"}, status_code=404)
    return HTMLResponse(render_wallet_html(card))


@app.get("/card/{sid}")
async def card(sid: str):
    s = SESSIONS.get(sid)
    if not s or not s.agent or not s.agent.state.card_path:
        return JSONResponse({"error": "No card yet"}, status_code=404)
    return FileResponse(s.agent.state.card_path, media_type="text/html")


# ---------------------------------------------------------------------------
# The family page: /share/<id>
# ---------------------------------------------------------------------------

def _share_json(rec: dict, owner: bool) -> JSONResponse:
    return JSONResponse({**ShareStore.public(rec), "owner": owner})


def _notify_owner(rec: dict, text: str, notice: str = "") -> None:
    s = SHARE_OWNERS.get(rec["id"])
    if s and s.agent:
        s.apply_share(rec, text)
        if notice:
            s.emit({"type": "notice", "text": notice})


@app.get("/share/{share_id}")
async def share_page(share_id: str):
    return FileResponse(STATIC / "share.html")


@app.get("/api/share/{share_id}")
async def share_get(share_id: str, owner: str = ""):
    try:
        rec = SHARES.get(share_id)
    except ShareError:
        return JSONResponse({"error": "This list was not found. It may have been deleted."}, status_code=404)
    return _share_json(rec, SHARES.is_owner(rec, owner))


@app.post("/api/share/{share_id}/claim")
async def share_claim(share_id: str, request: Request):
    body = await request.json()
    name = str(body.get("name", ""))
    try:
        rec, token = SHARES.claim(share_id, int(body.get("task_id", -1)), name, str(body.get("days", "any")),
                                  str(body.get("times", "any")), str(body.get("note", "")))
    except (ShareError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    row = next(t for t in rec["tasks"] if t["id"] == int(body["task_id"]))
    placed = bool(row["person"])   # their times fit, so their name is on it now
    _notify_owner(rec, f"{row['person']} signed up to help: {row['task']}" if placed else
                  f"{name.strip()} offered to help: {row['task']}")
    return JSONResponse({**ShareStore.public(rec), "claim_token": token, "task_id": row["id"], "placed": placed})


@app.post("/api/share/{share_id}/unclaim")
async def share_unclaim(share_id: str, request: Request):
    body = await request.json()
    token = str(body.get("token", ""))
    try:
        before = SHARES.get(share_id)
        task_id = int(body.get("task_id", -1))
        was = next((t["person"] for t in before["tasks"] if t["id"] == task_id), "")
        by_owner = SHARES.is_owner(before, token)
        rec = SHARES.unclaim(share_id, task_id, token)
    except (ShareError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    row = next(t for t in rec["tasks"] if t["id"] == task_id)
    _notify_owner(rec, f"Someone stepped back from: {row['task']}. It is open again.",
                  notice=_stepped_back_notice(was, row["task"], by_owner))
    return JSONResponse(ShareStore.public(rec))


def _stepped_back_notice(name: str, task: str, by_owner: bool) -> str:
    """A calm, non-urgent update for the owner. 911 is for emergencies; this is not one."""
    if by_owner:
        return f'You removed {name or "a name"} from "{task}". It\'s open again.'
    return (f'{name or "Someone"} can no longer help with "{task}". It\'s open again. That\'s okay. '
            "You can ask someone else, or leave it open for family to sign up.")


@app.post("/api/share/{share_id}/delete")
async def share_delete_route(share_id: str, request: Request):
    body = await request.json()
    try:
        SHARES.delete(share_id, str(body.get("owner", "")))
    except ShareError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    s = SHARE_OWNERS.pop(share_id, None)
    if s:
        s.share_id = s.owner_token = s.share_expires = None
        s.emit({"type": "plan", "plan": s.plan()})
    return JSONResponse({"deleted": True})


def _session_or_404(sid: str):
    s = SESSIONS.get(sid)
    return s if s and s.agent else None


@app.get("/table/{sid}.csv")
async def table_csv(sid: str):
    s = _session_or_404(sid)
    if not s:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return Response(s.agent.box.tasks_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="who-is-helping.csv"'})


@app.get("/table/{sid}")
async def table_print(sid: str):
    s = _session_or_404(sid)
    if not s:
        return JSONResponse({"error": "Not found"}, status_code=404)
    e = htmlmod.escape
    from just_in_case.tools import TASK_STATUS, task_status
    rows = "".join(f"<tr><td>{e(t['task'])}</td><td>{e(t['date'])}</td><td>{e(t['time'])}</td>"
                   f"<td>{e(t['person']) or '<em>Open: add your name</em>'}</td>"
                   f"<td>{TASK_STATUS[task_status(t)]}</td></tr>" for t in s.agent.state.helper_tasks)
    rows = rows or '<tr><td colspan="5">No rows yet.</td></tr>'
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="color-scheme" content="light only"><meta name="darkreader-lock">
<title>Who is helping</title>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
 body {{ font-family: "Atkinson Hyperlegible", Verdana, Arial, sans-serif; font-size: 16pt; color: #000; max-width: 9in; margin: .4in auto; }}
 h1 {{ font-size: 24pt; }} table {{ width: 100%; border-collapse: collapse; }}
 th, td {{ border: 2px solid #000; padding: .08in .12in; text-align: left; vertical-align: top; }}
 th {{ background: #eee; }} .print {{ margin-top: .3in; font: inherit; font-weight: 700; padding: .1in .35in; border: 0; border-radius: 10px; background: #1F6F78; color: #fff; }}
 @media print {{ .print {{ display: none; }} }}
</style></head><body>
<h1>Who is helping</h1>
<table><thead><tr><th>Task</th><th>Date</th><th>Time</th><th>Who is helping</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>
<p>In an emergency, call 911.</p>
<button class="print" onclick="window.print()">Print this table</button>
</body></html>""")


@app.get("/plan/{sid}")
async def plan_print(sid: str):
    """"Print my plan": the whole plan on one clean page."""
    from just_in_case.card import render_plan_html
    s = _session_or_404(sid)
    if not s:
        return JSONResponse({"error": "Not found"}, status_code=404)
    st, d = s.agent.state, s.agent.box.dir
    return HTMLResponse(render_plan_html(st, d, s.agent.box.today,
                                         s.plan()["help_lines"] if st.services_only() else None))


@app.get("/summary/{sid}")
async def summary_print(sid: str):
    """"My readiness summary": made only when the person asks, fresh each time, never stored."""
    from just_in_case.summary import render_summary_html
    s = _session_or_404(sid)
    if not s:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not s.summary_ok:  # opt in only: no yes, no summary
        return JSONResponse({"error": "The summary is only made after you say yes."}, status_code=403)
    return HTMLResponse(render_summary_html(s.agent.state, s.agent.box.dir, s.agent.box.today))


def visit_mode(example: bool) -> str:
    """Ruth's example when asked for; otherwise the live agent if an AI key is set up, else the guided planner."""
    if example:
        return "example"
    return "guided" if DEMO or not has_ai_key() or AI_LIMIT.reached() else "live"


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not signed_in(ws.cookies):  # the page's live connection needs the password too
        await ws.close(code=1008)
        return
    await ws.accept()
    s = Session(ws, mode=visit_mode(ws.query_params.get("example") == "1"))
    SESSIONS[s.id] = s

    async def writer():
        while True:
            await ws.send_json(await s.out.get())

    writer_task = asyncio.create_task(writer())
    tasks: set[asyncio.Task] = set()
    try:
        await s.start()
        s.emit({"type": "hello", "session": s.id, "demo": s.demo, "mode": s.mode, "greeting": GREETING, "agent_name": AGENT_NAME,
                "people_template": PEOPLE_TEMPLATE})   # blank: the page fills in each name, on the person's device
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "say" and str(msg.get("text", "")).strip():
                t = asyncio.create_task(s.handle_say(str(msg["text"])[:2000]))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
            elif msg.get("type") == "answer":
                s.answer(str(msg.get("id")), bool(msg.get("ok")), msg.get("choice"))
            elif msg.get("type") == "table" and isinstance(msg.get("row"), dict):
                s.edit_table(str(msg.get("action")), msg["row"])
            elif msg.get("type") == "share_create":
                s.share_create(msg.get("consent"), str(msg.get("label", "")))
            elif msg.get("type") == "share_delete":
                s.share_delete()
            elif msg.get("type") == "choose_helper":
                try:
                    s.choose_helper(int(msg.get("task_id")), int(msg.get("offer_id")))
                except (TypeError, ValueError):
                    pass
            elif msg.get("type") == "share_reconnect":
                s.share_reconnect(str(msg.get("id", ""))[:80], str(msg.get("owner", ""))[:80])
            elif msg.get("type") == "example_replies":
                s.example_replies()
            elif msg.get("type") == "plans_changed":
                s.plans_changed(str(msg.get("date", "")), str(msg.get("time", "")))
            elif msg.get("type") == "note_sent":   # the person says they sent a note from their own phone or email
                s.emit({"type": "activity", "kind": "human", "text": "You sent a note from your own phone or email"})
            elif msg.get("type") == "save":
                s.save()
            elif msg.get("type") in ("event_date", "step"):
                s.timeline_edit(msg)
            elif msg.get("type") == "summary_consent":
                s.summary_consent(msg.get("agree"))
            elif msg.get("type") == "review":
                s.review_edit(str(msg.get("action", "")))
            elif msg.get("type") == "next_step":
                s.next_step_edit(str(msg.get("need", ""))[:60], str(msg.get("action", "")))
            elif msg.get("type") == "item":
                s.answer_item(str(msg.get("need", "")), msg.get("ok") is True)
            elif msg.get("type") == "restore":
                s.restore(msg.get("state"))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "text": f"Could not start Just in Case: {e}"})
        except Exception:
            pass
    finally:
        for t in tasks:
            t.cancel()
        writer_task.cancel()
        await s.stop()
        SESSIONS.pop(s.id, None)
        if s.share_id:
            SHARE_OWNERS.pop(s.share_id, None)  # the shared list stays, as she agreed


app.mount("/static", StaticFiles(directory=STATIC), name="static")
