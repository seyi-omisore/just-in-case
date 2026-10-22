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
from just_in_case.prompts import GREETING
from just_in_case.state import CARD_SECTIONS, NEEDS
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
SESSIONS: dict[str, "Session"] = {}
SHARES = ShareStore(OUTPUT_DIR / "shared")
SHARE_OWNERS: dict[str, "Session"] = {}  # share id -> the owner's open visit
SHARES.purge_expired()


# ---------------------------------------------------------------------------
# Plain-language descriptions for the activity feed
# ---------------------------------------------------------------------------

def need_label(key: str) -> str:
    return NEEDS.get(key, key.replace("_", " "))


def describe_tool(short: str, data: dict) -> tuple[str, str] | None:
    """(kind, text) shown in the feed when the agent uses a tool."""
    need = need_label(data.get("need", "")) if data.get("need") else ""
    return {
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
    def __init__(self, ws: WebSocket):
        self.id = uuid.uuid4().hex
        self.ws = ws
        self.out: asyncio.Queue = asyncio.Queue()
        self.pending: dict[str, asyncio.Future] = {}
        self.agent = None
        self.lock = asyncio.Lock()  # one user message at a time
        # Each visit gets its own folder for the card, deleted when the visit ends.
        self.out_dir = OUTPUT_DIR / "sessions" / self.id
        self.share_id: str | None = None
        self.owner_token: str | None = None
        self.share_expires: float | None = None

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
        if short == "make_fridge_card":
            return {"name": data.get("name", ""), "contacts": data.get("contacts", []),
                    "medications": data.get("medications", []),
                    "pet": data.get("pet_name", ""), "pet_caregiver": data.get("pet_caregiver", "")}
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
                "flags": i.flags,
            })
        return {
            "items": items,
            "notes": state.helper_notes,
            "tasks": state.helper_tasks,
            "card": f"/card/{self.id}" if state.card_path else None,
            "table_csv": f"/table/{self.id}.csv",
            "table_print": f"/table/{self.id}",
            "share": {"url": f"/share/{self.share_id}",
                      "owner_url": f"/share/{self.share_id}?owner={self.owner_token}",
                      "expires": self.share_expires} if self.share_id else None,
        }

    # -- sharing (only with consent) -----------------------------------------
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
        for t in self.agent.state.helper_tasks:
            if t["id"] in people:
                t["person"] = people[t["id"]]
        self.agent.box.refresh_card()
        self.emit({"type": "activity", "kind": "heart", "text": activity})
        self.emit({"type": "plan", "plan": self.plan()})

    def edit_table(self, action: str, row: dict) -> None:
        from just_in_case.tools import ToolError
        try:
            self.agent.box.user_edit_task(action, row)
            self.emit({"type": "activity", "kind": "human", "text": {
                "add": "You added a row to your helper table",
                "update": "You changed a row in your helper table",
                "delete": "You removed a row from your helper table"}.get(action, "You edited your table")})
        except ToolError as e:
            self.emit({"type": "table_error", "text": str(e)})
        self.sync_share()
        self.emit({"type": "plan", "plan": self.plan()})

    # -- lifecycle -----------------------------------------------------------
    async def start(self):
        if DEMO:
            from web.demo_agent import DemoAgent
            self.agent = DemoAgent(self.approver, trace=self.on_trace, on_tool=self.on_tool,
                                   out_dir=self.out_dir)
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

    async def handle_say(self, text: str):
        async with self.lock:
            self.emit({"type": "busy", "on": True})
            try:
                await self.agent.send(text, on_text=lambda t: self.emit(
                    {"type": "agent", "text": t.removeprefix("\nJust in Case: ")}))
            except Exception as e:  # keep the page usable, say what happened
                self.emit({"type": "error", "text": f"Something went wrong on our side: {e}. "
                           "Please try sending your message again."})
            finally:
                self.emit({"type": "plan", "plan": self.plan()})
                self.emit({"type": "busy", "on": False})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def config():
    return JSONResponse({"demo": DEMO, "greeting": GREETING,
                         "needs_restart": code_fingerprint() != STARTED_WITH})


@app.get("/api/support")
async def support():
    """Verified support lines for the "People you can talk to" box."""
    lines = json.loads((DATA_DIR / "support_lines.json").read_text())["lines"]
    keep = ("name", "how_to_reach", "hours", "what", "kind", "url")
    return JSONResponse([{k: l.get(k, "") for k in keep} for l in lines])


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


def _notify_owner(rec: dict, text: str) -> None:
    s = SHARE_OWNERS.get(rec["id"])
    if s and s.agent:
        s.apply_share(rec, text)


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
    try:
        rec, token = SHARES.claim(share_id, int(body.get("task_id", -1)), str(body.get("name", "")))
    except (ShareError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    row = next(t for t in rec["tasks"] if t["id"] == int(body["task_id"]))
    _notify_owner(rec, f"{row['person']} signed up to help: {row['task']}")
    return JSONResponse({**ShareStore.public(rec), "claim_token": token, "task_id": row["id"]})


@app.post("/api/share/{share_id}/unclaim")
async def share_unclaim(share_id: str, request: Request):
    body = await request.json()
    try:
        rec = SHARES.unclaim(share_id, int(body.get("task_id", -1)), str(body.get("token", "")))
    except (ShareError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    row = next(t for t in rec["tasks"] if t["id"] == int(body["task_id"]))
    _notify_owner(rec, f"Someone stepped back from: {row['task']}. It is open again.")
    return JSONResponse(ShareStore.public(rec))


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
    rows = "".join(f"<tr><td>{e(t['task'])}</td><td>{e(t['date'])}</td><td>{e(t['time'])}</td>"
                   f"<td>{e(t['person']) or '<em>Open: add your name</em>'}</td></tr>" for t in s.agent.state.helper_tasks)
    rows = rows or '<tr><td colspan="4">No rows yet.</td></tr>'
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Who is helping</title>
<style>
 body {{ font-family: "Atkinson Hyperlegible", Verdana, Arial, sans-serif; font-size: 16pt; color: #000; max-width: 9in; margin: .4in auto; }}
 h1 {{ font-size: 24pt; }} table {{ width: 100%; border-collapse: collapse; }}
 th, td {{ border: 2px solid #000; padding: .08in .12in; text-align: left; vertical-align: top; }}
 th {{ background: #eee; }} .print {{ margin-top: .3in; font: inherit; font-weight: 700; padding: .1in .35in; border: 0; border-radius: 10px; background: #0072B2; color: #fff; }}
 @media print {{ .print {{ display: none; }} }}
</style></head><body>
<h1>Who is helping</h1>
<table><thead><tr><th>Task</th><th>Date</th><th>Time</th><th>Who is helping</th></tr></thead><tbody>{rows}</tbody></table>
<p>In an emergency, call 911.</p>
<button class="print" onclick="window.print()">Print this table</button>
</body></html>""")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    s = Session(ws)
    SESSIONS[s.id] = s

    async def writer():
        while True:
            await ws.send_json(await s.out.get())

    writer_task = asyncio.create_task(writer())
    tasks: set[asyncio.Task] = set()
    try:
        await s.start()
        s.emit({"type": "hello", "session": s.id, "demo": DEMO, "greeting": GREETING})
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
