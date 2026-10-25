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
    assert pressure_phrases(ready) == [] and "!" not in ready
    assert ready.startswith("Your plan is ready.") and "print" in ready  # says what they can do, no more


def test_helper_updates_are_described_as_non_urgent_with_911_first():
    home = client.get("/").text
    updates = home[home.index('id="notices-wrap"'):home.index('id="notices"')]
    assert "In an emergency, call 911." in updates and "not urgent" in updates
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
    table = page[page.index('class="compare-chart"'):page.index("</table>")]
    row = table[table.index("Calls for help in an emergency"):table.index("</tr>", table.index("Calls for help"))]
    cells = __import__("re").findall(r'<span class="mark mark-(\w+)">.*?</svg>([^<]+)</span>', row)
    assert cells == [("no", "No"), ("no", "No"), ("no", "No"), ("yes", "Yes")]   # Just in Case cannot call; a pendant can
    assert "Just in Case is a planning tool. In an emergency, always call 911." in page


# ---- Simple mode: the home screen --------------------------------------------

def test_home_is_the_default_and_shows_only_three_big_buttons():
    page = client.get("/").text
    home = page[page.index('id="panel-home"'):page.index('id="panel-help"')]
    assert "hidden" not in home.split(">", 1)[0]  # Home is shown first
    assert home.count('class="btn') == 3
    for label in ("Make my plan", "See my plan", "Get help now"):
        assert label in home
    # About starts hidden, and About and Features live in the small menu.
    assert "hidden" in page[page.index('id="panel-about"'):].split(">", 1)[0]
    menu = page[page.index('id="more-menu"'):page.index("</details>", page.index('id="more-menu"'))]
    assert 'href="#about"' in menu and 'href="#features"' in menu


def test_get_help_now_lists_911_988_and_mass_211():
    page = client.get("/").text
    help_panel = page[page.index('id="panel-help"'):page.index('id="panel-about"')]
    for number in ("tel:911", "tel:988", "tel:211"):
        assert number in help_panel
    assert "Mass 211" in help_panel


def test_plan_screen_starts_in_simple_view_with_back_next_and_dots():
    page = client.get("/").text
    assert 'class="chat simple"' in page            # simple is the default
    for part in ('id="step-back"', 'id="step-next"', 'id="step-dots"', 'id="view-simple"', 'id="view-full"'):
        assert part in page
    assert 'id="messages"' in page                   # the full chat is still there behind "Show details"
    js = client.get("/static/app.js").text
    assert "pageFor" in js
    assert "Simple view" in page and "Full view" in page  # clearly labeled switch
    assert "jic-view" in js                                # the choice is remembered on this device


# ---- Short text: two short sentences per card, grade 6 or so ---------------------

class _CardText(__import__("html.parser", fromlist=["HTMLParser"]).HTMLParser):
    """Collects each paragraph and list item. Text inside a 'Show details' box is skipped."""
    BLOCKS = ("p", "li", "dd", "figcaption")
    HIDE = ("script", "style", "svg", "table", "textarea")

    def __init__(self):
        super().__init__()
        self.out, self.buf, self.tag, self.skip, self.more_depth, self.depth = [], None, None, 0, 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDE:
            self.skip += 1
        if tag == "details":
            self.depth += 1
            if self.more_depth == 0 and "more" in dict(attrs).get("class", "").split():
                self.more_depth = self.depth
        if tag in self.BLOCKS and not self.skip and not self.more_depth:
            self.buf, self.tag = "", tag

    def handle_endtag(self, tag):
        if tag in self.HIDE:
            self.skip -= 1
        if tag == "details":
            if self.more_depth == self.depth:
                self.more_depth = 0
            self.depth -= 1
        if tag == self.tag and self.buf is not None:
            text = " ".join(self.buf.split())
            if text:
                self.out.append(text)
            self.buf, self.tag = None, None

    def handle_data(self, data):
        if self.buf is not None and not self.skip and not self.more_depth:
            self.buf += data


# Safety lines that must say exactly what they say.
EXACT_LINES = {
    "Just in Case will never ask for money, bank details, or your Social Security number.",
    "In an emergency, call 911. Feeling alone or unsafe? Call or text 988.",   # safety wording is never shortened
    "Leya Jacob Kurzhiparambil, Oluwaseyi Omisore, Joan Arches, and Satoshi Nagae",   # the sign-off: names are not simplified
    "Just in Case was built by a team at the University of Massachusetts Boston for the Applied AI Microcredential, hosted by the Paul English Applied Artificial Intelligence Institute.",  # Who we are: the team's exact words
    "Professor Emerita, College of Education and Human Development",    # a title, not simplified
    "PhD Student in Exercise and Health Sciences",                       # a title, not simplified
    "Haven is the app's AI planning helper. It suggests options, but you make every decision, and it never sends, shares, or books anything without your OK.",  # exact wording the team chose
}


def test_every_card_is_two_short_sentences_at_about_grade_six():
    import re
    from just_in_case.safeguards import reading_grade
    too_long = []
    for url in ("/", "/share/anything"):
        parser = _CardText()
        parser.feed(client.get(url).text)
        for text in parser.out:
            words = len(text.split())
            if words < 6 or text in EXACT_LINES:
                continue  # labels and one-word lines are fine
            sentences = len(re.findall(r"[.!?](?:\s|$)", text)) or 1
            if sentences > 2 or words > 30 or reading_grade(text) > 7:
                too_long.append(f"{url}: {text[:90]}")
    assert not too_long, "Move the extra words behind <details class=\"more\">:\n" + "\n".join(too_long)


def test_starters_are_tappable_sentences_in_everyday_words():
    import re
    page = client.get("/").text
    starters = page[page.index('id="starters"'):page.index('id="composer"')]
    pairs = re.findall(r'<button[^>]*data-text="([^"]+)"[^>]*>([^<]+)</button>', starters)
    assert 3 <= len(pairs) <= 4
    assert all(text == label for text, label in pairs)  # what you read is what gets sent
    wanted = ("need a ride home", "going away for two weeks", "feed my cat if I went to the hospital")
    assert all(any(w in text for text, _ in pairs) for w in wanted)
    js = client.get("/static/app.js").text
    assert "send(b.dataset.text)" in js  # tapping one starts that plan


def test_plan_is_a_short_list_with_status_labels_that_expand():
    js = client.get("/static/app.js").text
    for label in ("Added to your plan", "Waiting for your OK", "Not covered yet"):
        assert label in js
    assert "aria-expanded" in js and "aria-controls" in js  # tapping a line opens its details


def test_next_step_card_follows_the_users_yes_done_and_later():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        assert plan["next_step"] is None  # nothing is a step until the user says yes
        first = plan["items"][0]["need"]
        ws.send_json({"type": "item", "need": first, "ok": True})
        step = until(ws, "plan")["plan"]["next_step"]
        assert step["need"] == first and step["text"].endswith(".")
        ws.send_json({"type": "next_step", "need": first, "action": "later"})
        assert until(ws, "plan")["plan"]["next_step"]["snoozed"] is True  # the only one, so it comes back
        ws.send_json({"type": "next_step", "need": first, "action": "done"})
        assert until(ws, "plan")["plan"]["next_step"] == {"all_done": True}
        ws.send_json({"type": "next_step", "need": "not_a_need", "action": "done"})
        assert "not on your plan" in until(ws, "table_error")["text"]


def test_illustrations_exist_and_have_descriptive_alt_text():
    import re
    page = client.get("/").text
    for name in ("home", "clinic", "outage", "pet", "mail", "ready"):
        assert f'id="ill-{name}"' in page
    hero = re.search(r'<svg[^>]*class="ill ill-hero[^"]*"[^>]*aria-label="([^"]+)"', page)
    assert hero and len(hero.group(1).split()) >= 8 and 'role="img"' in hero.group(0)
    js = client.get("/static/app.js").text
    alts = re.findall(r'"(Illustration: [^"]+)"', js)
    assert len(alts) >= 4 and all(len(a.split()) >= 8 for a in alts)  # procedure, outage, pet, mail
    table = js[js.index("const ILLUSTRATION"):js.index("function illustration")]
    for need in ("ride_home_after_procedure", "power_outage", "pet_care_during_stay", "mail_while_away"):
        assert need in table


def test_make_my_plan_opens_with_one_visible_promise():
    page = client.get("/").text
    plan = page[page.index('id="panel-plan"'):]
    start = plan.index('<p class="promise"')
    promise = plan[start:plan.index("</p>", start)]
    assert "Just in Case never sends, shares, or books anything without your OK." in promise
    assert start < plan.index('aria-label="Conversation with Haven"')  # above the chat
    assert " hidden" not in promise.split(">", 1)[0]  # the line itself is never hidden


def test_privacy_page_matches_what_the_app_does():
    from web.share_store import KEEP_DAYS
    page = client.get("/").text
    privacy = page[page.index('id="panel-privacy"'):page.index('id="panel-plan"')]
    js = client.get("/static/app.js").text
    assert f"{KEEP_DAYS} days" in privacy                      # the same number the share store uses
    assert f"history: chatHistory.slice(-100)" in js and "last 100 chat messages" in privacy
    for button in ("Stop saving and delete", "Stop sharing and delete", "Save my progress", "Share with family"):
        assert button in privacy and button in page.replace(privacy, "")  # the page names buttons that exist
    for topic in ("never sell", "AI service", "close the page", "first names"):
        assert topic in privacy
    # Linked from the footer, the menu, and the shared page.
    assert 'href="#privacy"' in page[page.index('class="site-footer"'):]
    assert 'href="#privacy" data-tab="privacy"' in page
    assert 'href="/#privacy"' in client.get("/share/anything").text


def test_the_ai_toolkit_log_is_not_left_behind():
    import inspect
    import just_in_case.agent as agent_mod
    src = inspect.getsource(agent_mod)
    assert "CLAUDE_CONFIG_DIR" in src and "rmtree(self._private_dir" in src


def test_every_tap_target_has_a_48_pixel_floor_in_every_text_size():
    css = client.get("/static/style.css").text
    floor = css[css.rindex("/* ---- Tap targets"):]
    assert "min-height: 48px !important" in floor and "min-width: 48px !important" in floor
    for part in ("button", "summary", "select", "textarea", "a[href]"):
        assert part in floor.split("{")[0]          # the floor covers all of these
    assert "min(14rem, 100%)" in css and "flex-wrap: wrap; max-width: 100%" in css  # big text cannot scroll sideways


def test_changing_steps_is_announced_to_screen_readers_and_icons_are_hidden_from_them():
    page = client.get("/").text
    assert 'id="step-announce" class="sr-only" role="status"' in page
    assert "announceStep" in client.get("/static/app.js").text
    import re
    for m in re.finditer(r'<svg class="icon"[^>]*>', page):
        wrapped = re.search(r'<span[^>]*aria-hidden="true"[^>]*>\s*$', page[:m.start()])
        assert 'aria-hidden="true"' in m.group(0) or wrapped, m.group(0)  # decorative icons make no noise


def test_review_button_records_a_date_and_the_page_can_undo_it():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        assert plan["review"] is None                 # nothing to review until a part is added
        ws.send_json({"type": "item", "need": plan["items"][0]["need"], "ok": True})
        first = until(ws, "plan")["plan"]["review"]
        assert first["count"] == 0 and first["reviewed_today"] is False
        ws.send_json({"type": "review", "action": "mark"})
        review = until(ws, "plan")["plan"]["review"]
        assert review["count"] == 1 and review["reviewed_today"] is True and len(review["history"]) == 1
        ws.send_json({"type": "review", "action": "undo"})
        assert until(ws, "plan")["plan"]["review"]["count"] == 0
    page = client.get("/").text
    assert "Mark my plan as reviewed" in page and 'id="review-box"' in page


def test_readiness_summary_is_opt_in_and_never_stored():
    with client.websocket_connect("/ws") as ws:
        *_, plan = ruth_flow(ws)
        assert plan["summary_available"] is False and plan["summary_print"] is None  # nothing in the plan yet
        ws.send_json({"type": "item", "need": plan["items"][0]["need"], "ok": True})
        offered = until(ws, "plan")["plan"]
        assert offered["summary_available"] is True and offered["summary_print"] is None  # offered, not made
        sid = next(k for k in __import__("web.server", fromlist=["SESSIONS"]).SESSIONS)
        assert client.get(f"/summary/{sid}").status_code == 403                      # no yes, no summary
        ws.send_json({"type": "summary_consent", "agree": False})
        assert until(ws, "plan")["plan"]["summary_print"] is None                    # a no stays a no
        assert client.get(f"/summary/{sid}").status_code == 403
        ws.send_json({"type": "summary_consent", "agree": True})
        url = until(ws, "plan")["plan"]["summary_print"]
        assert url == f"/summary/{sid}"
        page = client.get(url).text
        assert "My readiness summary" in page and "Times reviewed:" in page
    assert client.get(url).status_code == 404                                        # gone when the visit ends
    page = client.get("/").text
    assert "Create my readiness summary" in page and 'id="summary-dialog"' in page   # the button asks first
    assert "summary-yes" in client.get("/static/app.js").text


def test_privacy_page_covers_the_readiness_summary_and_the_new_saved_details():
    page = client.get("/").text
    privacy = page[page.index('id="panel-privacy"'):page.index('id="panel-plan"')]
    assert "readiness summary is made only when you ask and say yes" in privacy
    assert "review dates, the day you started" in privacy and "first name only" in privacy



# ---- Safety rules that must stay true ----------------------------------------------

def _hidden_in_show_details(page: str) -> str:
    import re
    return " ".join(re.sub(r"<[^>]+>", " ", m) for m in re.findall(r'<details class="more">.*?</details>', page, re.S))


def test_911_and_988_bar_is_on_every_page_and_safety_words_are_never_behind_a_click():
    import re
    for url in ("/", "/share/anything"):
        page = client.get(url).text
        bar = page[page.index('class="safety-strip"'):page.index("</div>", page.index('class="safety-strip"'))]
        assert "In an emergency, call 911." in bar and "988" in bar and "hidden" not in bar.split(">")[0]
        hidden = _hidden_in_show_details(page)
        assert not re.search(r"911|988|emergency|cannot call|unsafe|wellness|crisis", hidden, re.I), hidden


def test_the_bar_sits_above_every_screen_and_is_never_inside_one():
    page = client.get("/").text
    bar_at = page.index('class="safety-strip"')
    first_screen = min(page.index(f'id="panel-{n}"') for n in ("home", "help", "about", "features", "privacy", "plan"))
    assert bar_at < first_screen                       # outside the screens, so switching screens never hides it
    assert 'class="safety-strip" role="note"' in page


def test_plan_warnings_stay_visible_and_every_consent_box_repeats_911():
    js = client.get("/static/app.js").text
    assert "li.insertBefore(ul, panel)" in js           # waitlist, escort rule, no phone: shown without a tap
    page = client.get("/").text
    for dialog in ("consent-dialog", "summary-dialog", "settings-dialog"):
        box = page[page.index(f'id="{dialog}"'):]
        box = box[:box.index("</dialog>")]
        assert "In an emergency" in box and "911" in box


# ---- Reading voice -----------------------------------------------------------------

def test_reading_voice_has_accent_female_male_sample_and_speed_controls():
    page = client.get("/").text
    box = page[page.index('id="voice-row"'):]
    box = box[:box.index("</fieldset>")]
    settings = page[page.index('id="settings-dialog"'):]
    assert page.index('id="settings-dialog"') < page.index('id="voice-row"') < settings.index("</dialog>") + page.index('id="settings-dialog"')  # behind the gear
    for part in ('id="voice-accent"', 'id="voice-kind"', 'id="voice-sample"', 'id="voice-slower"', 'id="voice-faster"', 'id="voice"'):
        assert part in box
    assert "Play sample" in box and "Slower" in box and "Faster" in box
    # Simple view shows the accent, female or male, Play sample, and Slower / Faster; only the full voice list is Full view only.
    full_only = [m for m in box.split("<")[1:] if "voice-full-only" in m]
    assert len(full_only) == 1            # the full voice list
    assert 'class="voice-speed"' in box   # Slower and Faster are always there
    css = client.get("/static/style.css").text
    assert ".simple-view .voice-full-only { display: none; }" in css


def test_reading_voice_script_groups_by_accent_and_never_guesses_gender():
    js = client.get("/static/app.js").text
    voice = js[js.index("Reading voice: accent"):js.index("function readAloudButton")]
    for tag, name in (("en-US", "American"), ("en-GB", "British"), ("en-AU", "Australian"), ("en-IN", "Indian"),
                      ("en-IE", "Irish"), ("en-ZA", "South African")):
        assert f'"{tag}": "{name}"' in voice
    for known in ("samantha", "daniel", "karen", "moira", "rishi"):
        assert known in voice.lower()
    assert "`${ACCENTS[k]} (${k})`" in voice                                # "American (en-US)", with its tag
    assert "we do not guess" in voice and "`Voice ${n}`" in voice          # unknown voices are just "Voice 1", "Voice 2"
    assert 'const SAMPLE = "Hi, I\'m here to help you make your plan."' in voice
    assert "SPEEDS = [0.7, 0.8, 0.9, 1.0, 1.1]" in voice and "u.pitch = 1.1" in voice
    assert 'localStorage.getItem("jic-voice-"' in voice and "sessionStorage" not in voice   # remembered on this device
    assert 'raw !== null' in voice                                         # a missing saved speed never reads as "slowest"


def test_demo_understands_many_ways_to_say_you_are_leaving_and_never_repeats_itself():
    from web.demo_agent import NEED_WORDS
    import re
    mail = dict(NEED_WORDS)["mail_while_away"]
    for phrase in ("im leaving for 2 weeks", "just leaving for 2 weeks", "I'm going away for two weeks", "heading out of town",
                   "taking a vacation", "I'll be gone a month", "visiting my daughter"):
        assert re.search(mail, phrase, re.I), phrase
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        replies = []
        for text in ("hmm", "blah"):                      # nothing the demo knows
            ws.send_json({"type": "say", "text": text})
            replies.append(until(ws, "agent")["text"])
        assert replies[0] != replies[1] and "only knows a few topics" in replies[1]


# ---- The agent's name ----------------------------------------------------------------

def test_the_agent_is_named_haven_in_one_place_and_the_app_stays_just_in_case():
    import pathlib
    from just_in_case.config import AGENT_NAME
    from just_in_case.prompts import GREETING, SYSTEM_PROMPT
    assert AGENT_NAME == "Haven"
    assert GREETING == ("Hi, I'm Haven, your planning helper from Just in Case. "
                        "A little planning now means less worry later. What's coming up for you?")
    root = pathlib.Path(__file__).resolve().parent.parent
    for f in [*root.glob("just_in_case/*.py"), *root.glob("web/*.py"), *root.glob("web/static/*")]:
        if f.suffix in {".py", ".js", ".html", ".css"} and f.name != "config.py":
            assert "Haven" not in f.read_text(), f"{f.name} types the name; use AGENT_NAME / {{{{AGENT_NAME}}}} instead"
    page = client.get("/").text
    assert "{{AGENT_NAME}}" not in page and 'window.JIC_AGENT_NAME = "Haven"' in page
    assert "<title>Just in Case</title>" in page and 'class="brand-name">Just in Case' in page   # the app keeps its name
    assert client.get("/api/config").json()["agent_name"] == "Haven"


def test_changing_the_name_in_one_place_renames_the_agent_on_the_page():
    import web.server as srv
    page = srv.render_page("index.html", "Sam")
    assert "Who is Sam?" in page and "Sam is the app's AI planning helper." in page and "Haven" not in page
    assert 'aria-label="Conversation with Sam"' in page and "What Sam is doing" in page and "Sam is thinking" in page
    assert 'window.JIC_AGENT_NAME = "Sam"' in page


def test_faq_explains_who_the_agent_is_in_the_words_the_team_chose():
    page = client.get("/").text
    faq = page[page.index('class="faq"'):]
    assert faq.index("Who is Haven?") < faq.index("Is it free?")          # first question
    assert ("Haven is the app's AI planning helper. It suggests options, but you make every decision, "
            "and it never sends, shares, or books anything without your OK.") in faq


def test_agent_is_told_it_is_a_digital_helper_and_never_a_person():
    from just_in_case.prompts import SYSTEM_PROMPT
    text = " ".join(SYSTEM_PROMPT.split())
    assert text.startswith("You are Haven, a digital planning helper inside the Just in Case app")
    assert "You are not a person. Never say or suggest that you are human" in text
    assert "say you are Haven, a digital planning helper" in text
    for safety in ("In an emergency, tell them to call 911.", "Call get_support_lines and share 988",
                   "No medical or mental health advice."):
        assert safety in text                                              # the safety rules are still there


def test_chat_names_the_agent_for_everyone_including_screen_readers():
    js = client.get("/static/app.js").text
    assert 'window.JIC_AGENT_NAME' in js and 'class="msg-name"' not in js and '"msg-name", AGENT' in js
    assert "your digital planning helper" in js and 'setAttribute("role", "img")' in js     # the avatar's alt text
    assert "Read ${AGENT}'s message aloud" in js and "Stop reading ${AGENT}'s message" in js  # Read aloud
    page = client.get("/").text
    assert "Meet Haven" in page[page.index('id="panel-about"'):page.index('id="panel-features"')]
    assert "Haven, your planning helper" in page[page.index('id="panel-features"'):page.index('id="panel-privacy"')]


def test_the_footer_has_no_team_section():
    page = client.get("/").text
    footer = page[page.index('class="site-footer"'):page.index("</footer>")]
    assert 'id="f-team"' not in footer and "Kurzhiparambil" not in footer and "Microcredential" not in footer
    assert 'href="#who"' not in footer
    for kept in ('id="f-help"', 'id="f-privacy"', 'id="f-access"', 'href="tel:911"', 'href="#privacy"'):
        assert kept in footer                                       # help numbers, privacy, and accessibility stay


def test_the_about_sign_off_uses_full_names_and_no_first_name_only_credit_is_left():
    page = client.get("/").text
    assert '<p class="why-sign">Leya Jacob Kurzhiparambil, Oluwaseyi Omisore, Joan Arches, and Satoshi Nagae</p>' in page
    assert "Leya, Seyi, Joan" not in page


# ---- Round 6: a calmer page ----------------------------------------------------------

def test_light_is_the_default_theme_and_old_saved_choices_are_ignored():
    for url, js in (("/", "/static/app.js"), ("/share/anything", "/static/share.js")):
        page, script = client.get(url).text, client.get(js).text
        boot = page[page.index("Pick the theme") if "Pick the theme" in page else page.index('localStorage.getItem("jic-theme'):]
        boot = boot[:boot.index("</script>")]
        assert 'localStorage.getItem("jic-theme-v2")' in boot and 't = "light";' in boot
        assert "prefers-color-scheme" not in boot                   # never follows the computer's dark setting
        assert 'localStorage.setItem("jic-theme-v2", t)' in script
        assert '"jic-theme"' not in page + script                   # the old key, which could hold "dark", is never read


def test_only_the_next_step_card_and_plan_items_keep_an_outline():
    import re
    css = client.get("/static/style.css").text
    calm = css[css.index("/* ---- Calm surfaces"):]
    first = calm[:calm.index("{")]
    for card in (".chat", ".fridge", ".helpers", ".about-box", ".faq", ".helper-box"):
        assert card in first                                     # outlines removed from these cards
    assert "border: 0; box-shadow: none; }" in calm[:calm.index("}") + 1]
    assert 'html:not([data-theme="contrast"])' in first          # high contrast keeps every outline
    assert re.search(r"\n\.next-step \{[^}]*border: 3px solid var\(--primary\)", calm)
    assert re.search(r"\n\.tag \{[^}]*border: 2px solid var\(--line\)", calm)
    assert "border: 2px solid var(--edge)" in calm               # text boxes still show where to type


def test_chat_is_soft_bubbles_and_the_starters_are_chips_above_the_message_box():
    import re
    css = client.get("/static/style.css").text
    chat = css[css.index("/* ---- Chat like a messaging app"):]
    for who in ("agent", "user"):
        rule = re.search(rf"\.msg-{who} \.msg-body \{{([^}}]*)\}}", chat).group(1)
        assert "border: 0" in rule and "background: var(--" in rule          # a soft bubble, no outline
    assert "overflow-wrap: anywhere" in chat and "min-width: 0" in chat     # long words never spill out
    starter = re.search(r"\n\.starter \{([^}]*)\}", chat).group(1)
    assert "border-radius: 999px" in starter and "border: 0" in starter and "min-height: 48px" in starter
    page = client.get("/").text
    assert page.index('id="starters"') < page.index('id="composer"')       # chips sit above the message box


def test_voice_text_size_and_colors_sit_behind_a_settings_gear():
    page = client.get("/").text
    header = page[page.index('class="site-header"'):page.index("</header>")]
    assert 'id="settings-open"' in header and "<span>Settings</span>" in header and "#i-gear" in header
    assert 'id="voice-accent"' not in header and 'id="bigger"' not in header and 'id="theme-dark"' not in header
    panel = page[page.index('id="settings-dialog"'):]
    panel = panel[:panel.index("</dialog>")]
    for part in ('id="voice-accent"', 'id="voice-slower"', 'id="voice-faster"', 'id="bigger"', 'id="smaller"',
                 'id="theme-light"', 'id="theme-dark"', 'id="theme-contrast"', 'id="settings-close"'):
        assert part in panel
    chat = page[page.index('class="chat simple"'):page.index("</section>", page.index('class="chat simple"'))]
    assert 'id="voice-row"' not in chat                                     # the chat itself stays uncluttered
    js = client.get("/static/app.js").text
    assert 'settings.showModal()' in js and 'settings.close()' in js


def test_helper_table_download_and_print_are_behind_more_options():
    page = client.get("/").text
    more = page[page.index('<details id="helper-more" class="more-options">'):]
    more = more[:more.index("</details>")]
    assert "<summary>More options" in more and " open" not in more.split(">", 1)[0]   # starts closed
    for part in ('id="helper-table"', 'id="task-form"', 'id="table-download"', 'id="table-print"'):
        assert part in more
    assert 'id="share-open"' not in more                                         # sharing stays in view
    js = client.get("/static/app.js").text
    assert '$("helper-more").open = true' in js and 'id="helper-count"' in page   # tasks are counted on the label


def test_your_plan_waits_for_its_first_item_and_988_never_hides():
    page = client.get("/").text
    assert '<aside id="side" class="side plan-empty"' in page                     # starts empty
    side = page[page.index('<aside id="side"'):page.index("</aside>")]
    assert "Your plan will appear here as we talk." in side
    css = client.get("/static/style.css").text
    assert ".side.plan-empty > :not(#plan-section):not(#support):not(.tour-tip) { display: none; }" in css
    assert ".side.plan-empty #plan-section > :not(#fridge-empty) { display: none; }" in css
    js = client.get("/static/app.js").text
    assert '$("side").classList.toggle("plan-empty", !plan.items.length && !(plan.tasks || []).length)' in js


def test_illustrations_are_original_flat_scenes_with_a_calm_ready_picture():
    import re
    page = client.get("/").text
    home = page[page.index('id="panel-home"'):page.index('id="panel-help"')]
    hero = re.search(r'<svg[^>]*class="ill ill-hero scene"[^>]*aria-label="([^"]+)"', home).group(1)
    assert "older" in hero and "house" in hero and "sunny" in hero                   # the person outside their house
    ready = re.search(r'<svg id="ready-ill"[^>]*aria-label="([^"]+)"[^>]*hidden', page)
    assert ready and "plan is ready" in ready.group(1)
    assert '$("ready-ill").toggleAttribute("hidden", !plan.ready)' in client.get("/static/app.js").text
    for name in ("home", "clinic", "outage", "pet", "mail", "ready"):
        sym = page[page.index(f'id="ill-{name}"'):][:5000]   # a shared drawing, or one drawn in the page so it can move
        assert "var(--ill-sky)" in sym and "<image" not in sym and "href=\"http" not in sym   # drawn here, in our colors


def test_first_time_tour_has_four_tips_shows_once_and_can_be_replayed():
    js = client.get("/static/app.js").text
    tour = js[js.index("const TOUR = ["):js.index("let tourTip")]
    assert tour.count("{ tab:") == 4
    for target in ('.home-buttons .btn-primary', '$("mic")', '$("plan-section")', "fridge card"):
        assert target in tour                                         # start, Speak, the plan, the fridge card
    assert '"Skip tour"' in js and '"Next"' in js and '"Done"' in js
    assert 'localStorage.setItem(TOUR_KEY, "1")' in js and "if (!toured && !EXAMPLE" in js   # once, never in the example
    assert 'if (raw === "tour")' in js
    page = client.get("/").text
    home = page[page.index('id="panel-home"'):page.index('id="panel-help"')]
    assert '<a href="#tour" class="tour-link">Show me how</a>' in home
    assert '<li><a href="#tour">Show me how</a></li>' in page[page.index('id="more-menu"'):]
    assert "position: fixed" not in client.get("/static/style.css").text.split("First-time tour")[1]  # never over the 911 bar


def test_havens_heart_pulses_while_thinking_but_not_when_motion_is_reduced():
    page = client.get("/").text
    typing = page[page.index('id="typing"'):page.index("</div>", page.index('id="typing"'))]
    assert 'class="thinking-heart"' in typing and "is thinking" in typing    # the words say it too
    css = client.get("/static/style.css").text
    assert "animation: heartbeat 1.6s ease-in-out 3;" in css and "scale(1.25)" in css   # a little more noticeable, still calm
    assert "@media (prefers-reduced-motion: reduce) { .thinking-heart { animation: none; } }" in css


def test_one_seasonal_tip_on_home_links_to_that_part_of_the_plan():
    page = client.get("/").text
    home = page[page.index('id="panel-home"'):page.index('id="panel-help"')]
    assert home.count('class="season-tip"') == 1
    assert "Winter is coming." in home and "Is your power outage plan ready?" in home
    assert '<a id="season-link" href="#plan-power_outage">Check it</a>' in home
    js = client.get("/static/app.js").text
    season = js[js.index("const SEASON"):js.index("// ---- first-time tour")]
    assert '"power_outage"' in season and '"mail_while_away"' in season       # only parts the app can plan
    assert "send(" not in season                                             # it never sends anything for you
    from just_in_case.safeguards import pressure_phrases
    for line in ("Winter is coming.", "Winter is here.", "Is your power outage plan ready?", "Going away this summer?"):
        assert pressure_phrases(line) == []


def test_leya_is_credited_with_the_full_name_everywhere():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for f in (root / "web/static/index.html", root / "README.md"):
        text = f.read_text()
        assert "Leya Jacob Kurzhiparambil" in text, f.name
        assert text.count("Leya Jacob") == text.count("Leya Jacob Kurzhiparambil"), f.name


def test_who_we_are_page_lists_the_team_and_is_linked_from_the_menu_and_footer():
    import re
    page = client.get("/").text
    who = page[page.index('id="panel-who"'):]
    who = who[:who.index("</section>")]
    assert " hidden" in who.split(">", 1)[0]                                       # not shown until asked for
    assert '<h1 id="who-title" class="page-title">Who we are</h1>' in who
    assert ("Just in Case was built by a team at the University of Massachusetts Boston for the Applied AI "
            "Microcredential, hosted by the Paul English Applied Artificial Intelligence Institute.") in who
    names = re.findall(r'<h2 class="team-name">([^<]+)</h2>\s*<p class="team-program">([^<]+)</p>', who)
    assert names == [("Leya Jacob Kurzhiparambil", "PhD Student in Gerontology"),
                     ("Oluwaseyi Omisore", "Undergraduate in Computer Science"),
                     ("Joan Arches", "Professor Emerita, College of Education and Human Development"),
                     ("Satoshi Nagae", "PhD Student in Exercise and Health Sciences")]
    roles = [re.findall(r'<li class="team-role">([^<]+)</li>', block) for block in who.split('<h2 class="team-name">')[1:]]
    assert roles == [["Services and safeguards lead", "Technical lead"], ["Technical lead"],
                     ["Evaluation and documentation lead"], ["Story, design, and presentation lead"]]   # one badge per role
    assert "<img" not in who                                                       # no photos
    assert '<li><a href="#who" data-tab="who">Who we are</a></li>' in page[page.index('id="more-menu"'):]
    assert 'href="#who"' not in page[page.index('id="panel-home"'):page.index('id="panel-help"')]  # not a main tab
    assert '"who"' in client.get("/static/app.js").text


# ---- Round 7 ----------------------------------------------------------------------------

def test_light_cannot_be_darkened_by_the_browser_and_files_are_never_stale():
    css = client.get("/static/style.css").text
    root = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    assert "color-scheme: only light;" in root                      # a browser's own dark mode may not repaint Light
    for url in ("/", "/share/anything"):
        r = client.get(url)
        assert '<meta name="color-scheme" content="only light">' in r.text
        assert r.headers["cache-control"] == "no-cache"
    for f in ("/static/style.css", "/static/app.js"):
        assert client.get(f).headers["cache-control"] == "no-cache"  # an old file never mixes with a new page
    import inspect
    import just_in_case.card as card, just_in_case.summary as summary
    for mod in (card, summary):
        assert '<meta name="color-scheme" content="only light">' in inspect.getsource(mod)   # printouts stay white


def test_links_and_link_buttons_keep_palette_colors_after_a_visit():
    import re
    css = client.get("/static/style.css").text
    assert "a:where(:link, :visited) { color: var(--link); }" in css
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        if ":visited" in sel and "color:" in body:
            assert "var(--" in body, sel                         # a visited link never gets a fixed or browser color
    page = client.get("/").text
    for cls in ("btn-primary", "btn-quiet", "brand", "menu-home"):
        assert re.search(rf"\.{cls}(?::where\([^)]*\))? \{{[^}}]*\bcolor: var\(--", css), cls   # each sets its own color
    assert 'fill="#1F6F78"' not in page                         # the logo follows the theme too


def test_who_we_are_is_a_main_tab_after_features_and_still_in_the_menu():
    import re
    page = client.get("/").text
    tabs = page[page.index('<nav id="tabs"'):page.index("</nav>", page.index('<nav id="tabs"'))]
    assert re.findall(r'data-tab="[^"]+">([^<]+)</a>', tabs) == ["About", "Features", "Who we are", "Make my plan"]
    assert '<li><a href="#who" data-tab="who">Who we are</a></li>' in page[page.index('id="more-menu"'):]
    js = client.get("/static/app.js").text
    assert '.more-menu a, .tabs a' in js and 'aria-current' in js            # the current tab is marked for screen readers


def test_display_button_in_the_header_has_text_size_and_colors_and_settings_keeps_them():
    page = client.get("/").text
    header = page[page.index('class="site-header"'):page.index("</header>")]
    disp = header[header.index('id="display-menu"'):header.index("</details>", header.index('id="display-menu"'))]
    assert "Display</summary>" in disp
    for part in ('data-size="smaller"', 'data-size="bigger"', 'data-theme-pick="light"', 'data-theme-pick="dark"', 'data-theme-pick="contrast"'):
        assert part in disp                                            # in the Display panel
    settings = page[page.index('id="settings-dialog"'):]
    settings = settings[:settings.index("</dialog>")]
    for part in ('data-size="bigger"', 'data-theme-pick="light"'):
        assert part in settings                                        # and still in Settings
    bar = page.index('class="safety-strip"')
    assert bar < page.index('class="site-header"') < page.index('id="panel-home"')   # the header sits on every screen
    js = client.get("/static/app.js").text
    assert 'querySelectorAll("[data-theme-pick]")' in js and 'querySelectorAll("[data-size]")' in js
    assert 'localStorage.setItem("jic-theme-v2", t)' in js and 'localStorage.setItem("jic-size"' in js   # remembered


def test_ruths_example_tells_the_newer_features_in_order_and_never_taps_yes_for_you():
    js = client.get("/static/app.js").text
    story = js[js.index("const STORY = ["):js.index("function storyTick")]
    order = ["Ruth taps a sentence", "one short question at a time", "sums up", "yes to her ride home", "calendar",
             "Someone's helping", "Call now", "yes to the rest", "plan is ready", "fridge card", "readiness summary",
             "marks her plan as reviewed"]
    at = [story.index(w) for w in order]
    assert at == sorted(at)                                                   # the story goes in this order
    assert ".click()" not in story.replace('document.querySelector(".starter").click()', "").replace("head.click()", "")
    assert ".slip-actions .btn-primary" in story and "story-next" in js     # yes buttons are ringed, never pressed
    assert 'EXAMPLE) cb.checked = ["contacts", "allergies", "papers"]' in js  # her card lists allergies and papers
    assert "Ruth's example is always Simple" in js
    page = client.get("/").text
    guide = page[page.index('id="story-guide"'):]
    assert " hidden" in guide.split(">", 1)[0] and "position: fixed" not in client.get("/static/style.css").text.split("Ruth's example: a story card")[1]


# ---- Round 7, part 2: Settings ---------------------------------------------------------

def test_settings_has_three_collapsible_sections_with_the_current_choice_and_911_first():
    import re
    page = client.get("/").text
    panel = page[page.index('id="settings-dialog"'):]
    panel = panel[:panel.index("</dialog>")]
    sections = re.findall(r'<details id="set-(\w+)" class="set-sec[^"]*"', panel)
    assert sections == ["voice", "size", "colors"]
    for name, now in (("Reading voice", "now-voice"), ("Text size", "now-size"), ("Colors", "now-colors")):
        assert f'<span class="set-title">{name}</span><span class="sr-only">: </span><span id="{now}"' in panel
    assert panel.index("In an emergency, call 911.") < panel.index('class="set-sections"')       # 911 stays first
    assert panel.rindex("These choices are saved only in this browser.") > panel.index('id="set-colors"')  # the note stays last
    for part in ('id="voice-accent"', 'id="voice-kind"', 'id="voice-slower"', 'id="voice-faster"', 'id="voice-sample"'):
        assert part in panel[panel.index('id="set-voice"'):panel.index('id="set-size"')]
    assert 'id="size-word"' in panel and 'data-size="bigger"' in panel and 'data-size="smaller"' in panel
    colors = panel[panel.index('id="set-colors"'):]
    assert colors.count('class="theme-choice"') == 3 and colors.count('class="theme-preview') == 3
    js = client.get("/static/app.js").text
    assert '$("now-colors").textContent' in js and '$("now-size").textContent' in js and '$("now-voice").textContent' in js


# ---- Round 7, part 3: Features and How it works ---------------------------------------

def test_features_are_four_groups_of_bubbles_that_open_one_at_a_time():
    import re
    page = client.get("/").text
    feat = page[page.index('id="panel-features"'):page.index('id="panel-privacy"')]
    groups = re.findall(r'<h2 id="fcat-\w+" class="fcat-title">([^<]+)</h2>', feat)
    assert groups == ["Make a plan", "Stay safe", "Get help", "Share with people you trust"]
    assert feat.count('<use href="#ill-cat-') == 4                               # one picture per group
    bubbles = re.findall(r'<button type="button" class="bubble" aria-expanded="false" aria-controls="(fb-\d+)">.*?'
                         r'<span class="bubble-name">([^<]+)</span>', feat, re.S)
    assert len(bubbles) >= 12
    for panel, name in bubbles:
        assert 2 <= len(name.split()) <= 4, name                                   # short names
        text = re.search(rf'<p id="{panel}" class="bubble-panel" hidden>([^<]+)</p>', feat).group(1)
        assert len(re.findall(r"[.!?](?:\s|$)", text)) == 1, text                    # one sentence each
        assert not re.search(r"911|988|emergency|crisis", text, re.I), text        # safety words are never behind a tap
    js = client.get("/static/app.js").text
    assert 'for (const o of bubbles) { o.setAttribute("aria-expanded", "false")' in js   # only one open at a time
    assert "<table" not in feat.replace('<table class="compare-chart">', "")           # the old big table is gone
    assert 'id="facts"' in feat and feat.count('class="fact-icon"') >= 7            # By the numbers: tiles with icons

def test_home_has_an_illustrated_how_it_works_row():
    import re
    page = client.get("/").text
    home = page[page.index('id="panel-home"'):page.index('id="panel-help"')]
    row = home[home.index('class="how-row"'):home.index("</ol>", home.index('class="how-row"'))]
    steps = re.findall(r'aria-label="(Illustration: [^"]+)"><use href="#ill-[\w-]+"/></svg><span>([^<]+)</span>', row)
    assert [t for _, t in steps] == ["Tell Haven what's coming up", "Get a simple plan", "Print your fridge card"]
    assert all(len(a.split()) >= 8 for a, _ in steps)
    assert home.index('class="home-buttons"') < home.index('class="how-row"') < home.index('id="season-tip"')
    for name in ("chat", "print", "cat-plan", "cat-safe", "cat-help", "cat-share"):
        sym = page[page.index(f'<symbol id="ill-{name}"'):]
        assert "var(--ill-sky)" in sym[:sym.index("</symbol>")]                      # drawn here, in theme colors


# ---- Round 7, part 4: gentle motion -----------------------------------------------------

def test_the_home_scene_moves_gently_then_settles_and_can_be_paused():
    import re
    page = client.get("/").text
    home = page[page.index('id="panel-home"'):page.index('id="panel-help"')]
    scene = home[home.index('<svg id="hero-scene"'):home.index("</svg>", home.index('id="ill-home"'))]
    for part in ("a-sun", "a-cloud1", "a-cloud2", "a-birds", "a-leaf1", "a-leaf2", "a-leaf3", "a-dress", "a-cat", 'class="tree"'):
        assert part in scene, part                                         # cat, clouds, birds, leaves, tree, dress, sun
    assert 'id="scene-btn"' in home and "Play scene" in home
    js = client.get("/static/app.js").text
    assert "const SCENE_MS = 9500;" in js and 'sceneButton("Pause", "pause")' in js and 'sceneButton("Play scene", "play")' in js
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js and "sceneBtn.hidden = stillOnly.matches" in js
    assert "playOnce(ride, 3500)" in js and 'playOnce($("ready-ill"), 1600)' in js   # the car drives, the check draws, once
    assert "a-car" in page and 'class="a-check" pathLength="1"' in page

def test_motion_is_slow_never_flashes_and_stops_for_reduce_motion():
    import re
    css = client.get("/static/style.css").text
    assert "*, *::before, *::after { animation: none !important; transition: none !important; }" in css
    for dur, count in re.findall(r"animation:[^;]*?\b(\d*\.?\d+)s\b[^;]*?(infinite|\b\d+\b)?;", css):
        assert float(dur) >= .3, dur                                        # nothing flashes 3 times a second
    assert "infinite" not in css                                            # nothing loops forever
    # Anything longer than five seconds is the home scene, which has a Pause button the whole time.
    for sel, body in re.findall(r"([^{}]+)\{([^}]*animation:[^}]*)\}", css):
        m = re.search(r"animation:\s*[\w-]+\s+(\d*\.?\d+)s(?:[^;]*?\s(\d+)(?:\s|;))?", body)
        if m:
            total = float(m.group(1)) * int(m.group(2) or 1)
            assert total <= 5.5 or "playing" in sel, (sel.strip(), total)


def test_the_911_and_988_bar_stays_in_sight_while_scrolling():
    css = client.get("/static/style.css").text
    assert ".safety-strip { position: sticky; top: 0; z-index: 30; }" in css
    assert "scroll-padding-top" in css


# ---- Round 7, part 5: the tour video ---------------------------------------------------

def test_about_has_a_one_minute_tour_with_captions_on_and_no_autoplay():
    import re, pathlib
    page = client.get("/").text
    about = page[page.index('id="panel-about"'):page.index('id="panel-features"')]
    tour = about[about.index('id="tour-video"'):about.index("</section>", about.index('id="tour-video"'))]
    assert "Watch a 1-minute tour" in tour
    video = re.search(r"<video[^>]*>", tour).group(0)
    assert "controls" in video and "autoplay" not in video and 'poster="/static/img/tour-poster.jpg"' in video
    assert '<source src="/static/img/tour.mp4" type="video/mp4">' in tour
    assert '<track kind="captions" src="/static/img/tour.vtt" srclang="en" label="English" default>' in tour
    assert 'id="tour-play"' in tour and 'id="tour-fallback"' in tour and tour.count("<li>") >= 6   # steps if the video is missing
    js = client.get("/static/app.js").text
    assert 'tr.mode = "showing"' in js and "tourMissing" in js and '"Pause"' in js and ".play()" in js
    img = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "img"
    for name in ("tour.mp4", "tour.vtt", "tour-poster.jpg"):
        assert (img / name).exists(), name
    assert (img / "tour.mp4").stat().st_size < 10 * 1024 * 1024
    vtt = (img / "tour.vtt").read_text()
    assert vtt.startswith("WEBVTT") and "Tell Haven what's coming up." in vtt and "Print your fridge card." in vtt
    assert client.get("/static/img/tour.vtt").headers["content-type"].startswith("text/vtt")

def test_the_tour_script_records_ruths_example_at_phone_size_in_light():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "scripts" / "make_tour_video.py").read_text()
    for part in ('"--demo"', "PHONE_W, PHONE_H = 390, 844", "record_video_dir", '"jic-theme-v2", "light"',
                 "?example=1", "tour.vtt", "tour-poster.jpg", "MAX_MB = 10", "showTap"):
        assert part in src, part
    readme = (pathlib.Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert "python scripts/make_tour_video.py" in readme
