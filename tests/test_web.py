"""Website tests, using the scripted demo agent (no AI, no cost). Run: pytest -q"""

import os

os.environ["JIC_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from web.server import app  # noqa: E402

client = TestClient(app)


def until(ws, kind):
    """Read messages until one of the given type arrives; return it."""
    for _ in range(200):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"never got {kind}")


def test_pages_load():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/config").json()["demo"] is True


def approve(ws):
    a = until(ws, "approval")
    ws.send_json({"type": "answer", "id": a["id"], "ok": True})
    return a


def ruth_flow(ws, pet=True, sections=("contacts",)):
    until(ws, "hello")
    first = "I'm Ruth. I have a procedure next month and may stay two nights in the hospital."
    ws.send_json({"type": "say", "text": first + (" My cat needs feeding." if pet else "")})
    needs = approve(ws)
    until(ws, "agent")
    ws.send_json({"type": "say", "text": "Yes, I could ask Samantha."})
    note = approve(ws)
    row = approve(ws)
    choose = until(ws, "approval")
    assert choose["kind"] == "choose_card_sections"
    ws.send_json({"type": "answer", "id": choose["id"], "ok": True, "choice": list(sections)})
    if "contacts" in sections:
        until(ws, "agent")
        ws.send_json({"type": "say", "text": "Samantha Lee 617-555-0142"})
    card = approve(ws)
    until(ws, "agent")
    return needs, note, row, card, until(ws, "plan")["plan"]


def test_full_flow_with_all_checkpoints():
    with client.websocket_connect("/ws") as ws:
        needs, note, row, card, plan = ruth_flow(ws)
        assert client.get(plan["card"]).status_code == 200
    assert [needs["kind"], note["kind"], row["kind"], card["kind"]] == [
        "confirm_needs", "draft_helper_note", "add_helper_task", "make_fridge_card"]
    items = {i["need"]: i for i in plan["items"]}
    assert items["ride_home_after_procedure"]["name"] == "FriendshipWorks Medical Escort"
    assert items["mail_while_away"]["name"] == "USPS Hold Mail"
    assert items["pet_care_during_stay"]["name"] == "Samantha"
    assert plan["tasks"][0]["person"] == "Samantha"
    assert plan["card"]


def test_no_pet_mentioned_means_no_pet_planned():
    with client.websocket_connect("/ws") as ws:
        needs, *_ , plan = ruth_flow(ws, pet=False)
        page = client.get(plan["card"]).text
    assert "pet_care_during_stay" not in [n["key"] for n in needs["data"]["needs"]]
    assert "My pet" not in page and "medications" not in page.lower()


def test_saying_no_to_a_checkpoint_stops_it():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "say", "text": "I have a procedure next month."})
        a1 = until(ws, "approval")
        ws.send_json({"type": "answer", "id": a1["id"], "ok": False})
        reply = until(ws, "agent")
        assert "which parts" in reply["text"].lower()
        plan = until(ws, "plan")["plan"]
        assert plan["items"] == []


def test_crisis_message_shares_988_and_does_not_plan():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "say", "text": "No one would notice if something happened to me."})
        reply = until(ws, "agent")
        assert "988" in reply["text"]
        assert until(ws, "plan")["plan"]["items"] == []


def test_unknown_card_is_not_served():
    assert client.get("/card/not-a-session").status_code == 404


def test_support_lines_endpoint_lists_988_and_boston_police():
    lines = client.get("/api/support").json()
    text = " ".join(l["how_to_reach"] for l in lines)
    assert "988" in text and "617-343-4200" in text


def test_card_page_has_911_banner_and_only_user_details():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        page = client.get(plan["card"]).text
    assert "IN AN EMERGENCY, CALL 911" in page and "Print this card" in page
    assert "Samantha Lee" in page and "617-555-0142" in page
    assert "Fifi" not in page and "Marion" not in page


def test_user_edits_table_and_downloads_spreadsheet():
    with client.websocket_connect("/ws") as ws:
        sid = until(ws, "hello")["session"]
        ws.send_json({"type": "table", "action": "add",
                      "row": {"task": "Drive me home", "person": "Leo", "date": "Nov 14", "time": "2 pm"}})
        plan = until(ws, "plan")["plan"]
        assert plan["tasks"][0]["task"] == "Drive me home"
        ws.send_json({"type": "table", "action": "add", "row": {"task": "", "person": ""}})
        assert "needs a task" in until(ws, "table_error")["text"].lower()
        csv_text = client.get(f"/table/{sid}.csv").text
        assert "Drive me home,Nov 14,2 pm,Leo" in csv_text
        assert "Drive me home" in client.get(f"/table/{sid}").text
    assert client.get(f"/table/{sid}.csv").status_code == 404


def test_card_files_are_deleted_when_the_visit_ends():
    from just_in_case.config import OUTPUT_DIR
    with client.websocket_connect("/ws") as ws:
        sid = until(ws, "hello")["session"]
    assert not (OUTPUT_DIR / "sessions" / sid).exists()


def test_card_checklist_starts_unticked_and_controls_questions():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws, sections=())
        page = client.get(plan["card"]).text
    assert "IN AN EMERGENCY, CALL 911" in page
    assert "People to contact" not in page and "My medications" not in page and "My plan" not in page


def test_open_rows_from_the_page():
    with client.websocket_connect("/ws") as ws:
        sid = until(ws, "hello")["session"]
        ws.send_json({"type": "table", "action": "add", "row": {"task": "Rides to PT", "person": ""}})
        assert until(ws, "plan")["plan"]["tasks"][0]["person"] == ""
        assert "Open: add your name" in client.get(f"/table/{sid}").text


def test_restart_warning_when_code_changes_under_a_running_server(monkeypatch):
    import web.server as srv
    assert client.get("/api/config").json()["needs_restart"] is False
    monkeypatch.setattr(srv, "STARTED_WITH", "old-version")
    assert client.get("/api/config").json()["needs_restart"] is True


def test_spreadsheet_is_a_real_spreadsheet_not_the_page():
    with client.websocket_connect("/ws") as ws:
        sid = until(ws, "hello")["session"]
        ws.send_json({"type": "table", "action": "add", "row": {"task": "Feed Fifi"}})
        until(ws, "plan")
        r = client.get(f"/table/{sid}.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("Task,Date,Time,Who is helping") and "<html" not in r.text
