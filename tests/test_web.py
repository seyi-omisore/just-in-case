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


def test_see_an_example_runs_the_ruth_demo_on_a_live_server(monkeypatch):
    import web.server as srv
    monkeypatch.setattr(srv, "DEMO", False)
    with client.websocket_connect("/ws?example=1") as ws:
        assert until(ws, "hello")["demo"] is True
        ws.send_json({"type": "say", "text": "I'm Ruth. I have a procedure next month."})
        assert until(ws, "approval")["kind"] == "confirm_needs"  # the real checkpoint still asks


def test_progress_starts_at_zero_and_fills_in_as_the_user_says_yes():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        # The agent found something for all 4 parts, but the user has not said yes yet.
        assert plan["progress"] == {"covered": 0, "total": 4} and plan["ready"] is False
        seen = []
        for item in plan["items"]:
            ws.send_json({"type": "item", "need": item["need"], "ok": True})
            seen.append(until(ws, "plan")["plan"]["progress"]["covered"])
    assert seen == [1, 2, 3, 4]


def test_no_thanks_takes_a_part_off_the_plan_and_tells_the_agent_next_turn():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        ws.send_json({"type": "item", "need": "spare_key_access", "ok": False})
        after = until(ws, "plan")["plan"]
    assert "spare_key_access" not in [i["need"] for i in after["items"]]
    assert after["progress"] == {"covered": 0, "total": 4}


def test_pages_say_we_never_ask_for_money_or_ssn():
    line = "Just in Case will never ask for money, bank details, or your Social Security number."
    assert line in client.get("/").text
    assert line in client.get("/share/anything").text


def test_services_carry_their_checked_date_from_the_data():
    import json
    from just_in_case.config import DATA_DIR
    svc = {s["id"]: s for s in json.loads((DATA_DIR / "services.json").read_text())["services"]}
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
    items = {i["need"]: i for i in plan["items"]}
    assert items["ride_home_after_procedure"]["checked"] == svc["svc_fw_escort"]["last_verified"]
    assert items["spare_key_access"]["checked"] == ""  # preparations have no date


def test_agent_asks_in_second_person_but_the_note_stays_in_the_users_voice():
    import re
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "say", "text": "I have a procedure next month."})
        approve(ws)
        q = until(ws, "agent")["text"]
        assert "check in on you the evening after your procedure" in q
        assert not re.search(r"\b(me|my|I'm)\b", q)
        ws.send_json({"type": "say", "text": "Yes, I could ask Samantha."})
        note = until(ws, "approval")
    assert "check in on me the evening after my procedure" in note["data"]["text"]


def test_no_one_to_ask_shows_services_only_with_council_on_aging_and_211():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "say", "text": "I have a procedure next month."})
        approve(ws)
        until(ws, "agent")
        ws.send_json({"type": "say", "text": "I don't have anyone to ask."})
        choose = until(ws, "approval")
        assert choose["kind"] == "choose_card_sections"  # no note, no helper row
        ws.send_json({"type": "answer", "id": choose["id"], "ok": True, "choice": []})
        approve(ws)  # the card itself
        reply = until(ws, "agent")["text"]
        plan = until(ws, "plan")["plan"]
    assert "Council on Aging" in reply and "2-1-1" in reply
    assert plan["services_only"] is True and plan["tasks"] == []
    assert {l["name"] for l in plan["help_lines"]} and "2-1-1" in " ".join(l["how_to_reach"] for l in plan["help_lines"])


def test_recap_comes_before_the_next_words_and_lists_the_plan():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "say", "text": "I have a procedure next month."})
        approve(ws)
        until(ws, "agent")
        ws.send_json({"type": "say", "text": "I'd rather not bother anyone."})
        choose = until(ws, "approval")
        ws.send_json({"type": "answer", "id": choose["id"], "ok": True, "choice": []})
        approve(ws)
        seen = []
        while not seen or seen[-1]["type"] != "agent":
            m = ws.receive_json()
            if m["type"] in ("recap", "agent"):
                seen.append(m)
    assert [m["type"] for m in seen] == ["recap", "agent"]
    assert seen[0]["text"].startswith("So far, we've planned:")
    assert "FriendshipWorks Medical Escort" in seen[0]["text"] and "Key lockbox" in seen[0]["text"]


def test_save_and_continue_later_in_a_new_visit():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        ws.send_json({"type": "save"})
        saved = until(ws, "saved")["state"]
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "restore", "state": saved})
        assert until(ws, "restored")["skipped"] == []
        assert until(ws, "recap")["text"].startswith("So far, we've planned:")
        again = until(ws, "plan")["plan"]
        ws.send_json({"type": "restore", "state": saved})  # only once, at the start
        assert "start of a visit" in until(ws, "table_error")["text"]
    assert [i["name"] for i in again["items"]] == [i["name"] for i in plan["items"]]
    assert again["card"]


def test_helping_someone_else_asks_if_they_agree_first():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "say", "text": "I'm helping someone else make a plan."})
        a = until(ws, "approval")
        assert a["kind"] == "confirm_helping_someone"
        ws.send_json({"type": "answer", "id": a["id"], "ok": False})
        assert "talk with the person you're helping first" in until(ws, "agent")["text"]
        ws.send_json({"type": "say", "text": "We talked. She has a procedure next month."})
        a = until(ws, "approval")
        assert a["kind"] == "confirm_helping_someone"
        ws.send_json({"type": "answer", "id": a["id"], "ok": True})
        assert until(ws, "approval")["kind"] == "confirm_needs"


def test_print_my_plan_is_one_clean_page_with_911_first():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        page = client.get(plan["plan_print"]).text
    assert page.index("IN AN EMERGENCY, CALL 911") < page.index("My plan</h2>")
    assert "FriendshipWorks Medical Escort" in page and "617-482-1510" in page
    assert "Info checked" in page and "Who is helping" in page and "Samantha" in page
    assert "617-555-0142" not in page  # card-only details (contacts) stay on the card
    assert client.get("/plan/not-a-session").status_code == 404


def test_plan_ready_is_said_once_calmly_when_every_need_is_covered():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "say", "text": "I have a procedure next month."})
        approve(ws)
        until(ws, "agent")
        ws.send_json({"type": "say", "text": "I'd rather not bother anyone."})
        choose = until(ws, "approval")
        ws.send_json({"type": "answer", "id": choose["id"], "ok": True, "choice": []})
        approve(ws)
        kinds = []
        while "agent" not in kinds:
            kinds.append(ws.receive_json()["type"])
        plan = until(ws, "plan")["plan"]
        assert "ready" not in kinds and plan["ready"] is False  # nothing confirmed yet
        for item in plan["items"]:
            ws.send_json({"type": "item", "need": item["need"], "ok": True})
        later = []
        while len([k for k in later if k == "plan"]) < len(plan["items"]):
            later.append(ws.receive_json()["type"])
        ws.send_json({"type": "table", "action": "add", "row": {"task": "Rides"}})
        final = until(ws, "plan")["plan"]
    assert later.count("ready") == 1 and final["ready"] is True


def test_ready_message_has_no_pressure():
    from just_in_case.safeguards import pressure_phrases
    import web.server as srv
    s = srv.Session.__new__(srv.Session)
    out = []
    s.emit = out.append
    s.recapped, s.ready_said = (), False

    class A:  # just enough of an agent for the message
        from just_in_case.state import PlanItem, PlanState
        state = PlanState(confirmed_needs=["power_outage"], items=[PlanItem("power_outage", "preparation", None, "Kit", user_ok=True)])
        class box:
            from datetime import date
            today = date(2026, 10, 23)
            class dir:
                by_id, support_lines = {}, []
    s.agent, s.share_id, s.owner_token, s.share_expires, s.id = A, None, None, None, "x"
    s.maybe_recap()
    ready = next(m["text"] for m in out if m["type"] == "ready")
    assert pressure_phrases(ready) == [] and "nothing you need to do" in ready.lower()


def test_helper_updates_are_described_as_non_urgent_with_911_first():
    home = client.get("/").text
    updates = home[home.index('id="notices-wrap"'):home.index('id="notices"')]
    assert "In an emergency, call 911." in updates and "not urgent" in updates
    features = home[home.index("<h3>Helper updates</h3>"):]
    assert features.index("911") < features.index("non-urgent")
    consent = home[home.index('class="consent-facts"'):]
    assert consent.index("call 911") < consent.index("What we save")
    family = client.get("/share/anything").text
    assert "In an emergency, call 911.</strong> This list is for everyday, non-urgent help." in family


def test_every_button_has_visible_words_not_just_an_icon_or_color():
    from html.parser import HTMLParser

    class Buttons(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.found = [], []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "button" or (tag == "a" and "btn" in (a.get("class") or "")):
                self.stack.append([a.get("id") or a.get("class"), ""])

        def handle_endtag(self, tag):
            if tag in ("button", "a") and self.stack:
                self.found.append(tuple(self.stack.pop()))

        def handle_data(self, data):
            for b in self.stack:
                b[1] += data

    for page in ("/", "/share/anything"):
        p = Buttons()
        p.feed(client.get(page).text)
        assert p.found, page
        icon_only = [name for name, words in p.found if not words.strip()]
        assert not icon_only, f"{page}: buttons with no visible words: {icon_only}"


def test_by_the_numbers_are_counted_from_the_app():
    import json
    from just_in_case.config import DATA_DIR
    from just_in_case.tools import GATED_TOOLS
    f = client.get("/api/facts").json()
    services = [s for s in json.loads((DATA_DIR / "services.json").read_text())["services"] if not s["sample"]]
    assert f["services"] == len(services) and f["free_services"] == sum(s["cost"] == "free" for s in services)
    assert f["checkpoints"] == len(GATED_TOOLS) and f["safeguard_tests"] >= 50


def test_faq_says_this_is_not_an_emergency_service():
    page = client.get("/").text
    faq = page[page.index('class="faq"'):]
    answer = faq[faq.index("Is this an emergency service?"):]
    assert "No. In an emergency, always call 911." in answer[:300]
    assert "Example story" in page and "made-up example" in page  # Ruth is labeled


def test_comparison_is_honest_that_pendants_can_call_for_help():
    page = client.get("/").text
    table = page[page.index('class="compare-table"'):page.index("</table>")]
    row = table[table.index("Calls for help in an emergency"):table.index("</tr>", table.index("Calls for help"))]
    assert "No. Always call 911" in row and "Yes, that is what it is for" in row
