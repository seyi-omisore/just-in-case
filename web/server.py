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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from just_in_case.config import DATA_DIR, OUTPUT_DIR
from just_in_case.prompts import GREETING
from just_in_case.state import NEEDS

STATIC = Path(__file__).parent / "static"
DEMO = os.getenv("JIC_DEMO") == "1"

app = FastAPI(title="Just in Case")
SESSIONS: dict[str, "Session"] = {}


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
        self.emit({"type": "plan", "plan": self.plan()})

    async def approver(self, short: str, data: dict) -> bool:
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
        if short == "make_fridge_card":
            return {"name": data.get("name", ""), "contacts": data.get("contacts", []),
                    "medications": data.get("medications", []),
                    "pet": data.get("pet_name", ""), "pet_caregiver": data.get("pet_caregiver", "")}
        return data

    def answer(self, aid: str, ok: bool) -> None:
        fut = self.pending.get(aid)
        if fut and not fut.done():
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
            "card": f"/card/{self.id}" if state.card_path else None,
        }

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
    return JSONResponse({"demo": DEMO, "greeting": GREETING})


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
                s.answer(str(msg.get("id")), bool(msg.get("ok")))
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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
