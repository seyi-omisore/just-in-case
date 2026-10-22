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


def test_full_ruth_flow_with_three_checkpoints():
    with client.websocket_connect("/ws") as ws:
        hello = until(ws, "hello")
        assert hello["demo"] is True

        ws.send_json({"type": "say", "text": "I have a procedure next month."})
        a1 = until(ws, "approval")
        assert a1["kind"] == "confirm_needs"
        ws.send_json({"type": "answer", "id": a1["id"], "ok": True})
        until(ws, "agent")

        ws.send_json({"type": "say", "text": "Yes, I'd ask Marion and Doris."})
        a2 = until(ws, "approval")
        assert a2["kind"] == "draft_helper_note"
        ws.send_json({"type": "answer", "id": a2["id"], "ok": True})
        until(ws, "agent")

        ws.send_json({"type": "say", "text": "Marion 617-555-0142"})
        a3 = until(ws, "approval")
        assert a3["kind"] == "make_fridge_card"
        ws.send_json({"type": "answer", "id": a3["id"], "ok": True})
        until(ws, "agent")

        plan = until(ws, "plan")["plan"]
        needs = {i["need"]: i for i in plan["items"]}
        assert needs["ride_home_after_procedure"]["name"] == "FriendshipWorks Medical Escort"
        assert needs["mail_while_away"]["name"] == "USPS Hold Mail"
        assert needs["pet_care_during_stay"]["name"] == "Marion and Doris"
        assert plan["card"] and client.get(plan["card"]).status_code == 200


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
