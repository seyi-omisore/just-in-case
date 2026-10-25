"""Sharing the helper list: consent, tokens, sign-ups, deletion. Run: pytest -q"""

import os
import time

import pytest

os.environ["JIC_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from web.server import SHARES, app  # noqa: E402
from web.share_store import ShareError, ShareStore  # noqa: E402

ROWS = [{"id": 1, "task": "Rides to PT", "date": "Tuesdays", "time": "", "person": ""},
        {"id": 2, "task": "Feed the dog", "date": "", "time": "", "person": "Andre"}]


@pytest.fixture
def store(tmp_path):
    return ShareStore(tmp_path)


def test_links_are_long_random_and_owner_token_is_separate(store):
    rec, owner = store.create("Gloria", ROWS)
    rec2, owner2 = store.create("Gloria", ROWS)
    assert rec["id"] != rec2["id"] and len(rec["id"]) >= 20 and owner != owner2
    assert owner not in (store.folder / f"{rec['id']}.json").read_text()  # only a hash is saved


def test_only_the_list_is_saved(store):
    rec, _ = store.create("Gloria", ROWS)
    saved = (store.folder / f"{rec['id']}.json").read_text()
    for word in ("medication", "contacts", "phone", "chat"):
        assert word not in saved


def test_helper_signs_up_and_only_they_can_remove_their_name(store):
    rec, owner = store.create("Gloria", ROWS)
    rec, token = store.claim(rec["id"], 1, "Leo")
    assert rec["tasks"][0]["person"] == "Leo"
    with pytest.raises(ShareError, match="already helping"):
        store.claim(rec["id"], 1, "Someone else")
    with pytest.raises(ShareError, match="only remove your own"):
        store.unclaim(rec["id"], 1, "wrong-token")
    assert store.unclaim(rec["id"], 1, token)["tasks"][0]["person"] == ""
    store.claim(rec["id"], 1, "Leo")
    assert store.unclaim(rec["id"], 1, owner)["tasks"][0]["person"] == ""  # owner can too


def test_only_owner_can_delete(store):
    rec, owner = store.create("", ROWS)
    with pytest.raises(ShareError):
        store.delete(rec["id"], "not-the-owner")
    store.delete(rec["id"], owner)
    with pytest.raises(ShareError):
        store.get(rec["id"])


def test_lists_expire(tmp_path):
    s = ShareStore(tmp_path, keep_days=0)
    rec, _ = s.create("", ROWS)
    time.sleep(0.01)
    with pytest.raises(ShareError):
        s.get(rec["id"])


def test_bad_ids_cannot_reach_other_files(store):
    for bad in ("../secrets", "a/b", ""):
        with pytest.raises(ShareError):
            store.get(bad)


def test_owner_edits_keep_helper_signups(store):
    rec, _ = store.create("", ROWS)
    rec, _ = store.claim(rec["id"], 1, "Leo")
    rows = [dict(ROWS[0], person="Leo", time="2 pm"), ROWS[1]]
    rec = store.update_tasks(rec["id"], rows)
    assert rec["tasks"][0]["claim_hash"]  # Leo can still remove himself


# ---- through the website ------------------------------------------------------

client = TestClient(app)


def until(ws, kind):
    for _ in range(200):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(kind)


def test_sharing_needs_consent_then_family_can_sign_up_live():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "table", "action": "add", "row": {"task": "Rides to PT", "date": "Tuesdays"}})
        until(ws, "plan")
        ws.send_json({"type": "share_create", "consent": False})
        assert "needs your OK" in until(ws, "table_error")["text"]
        ws.send_json({"type": "share_create", "consent": True, "label": "Gloria"})
        share = until(ws, "plan")["plan"]["share"]
        sid = share["url"].split("/")[-1]

        page = client.get(f"/api/share/{sid}").json()
        assert page["label"] == "Gloria" and page["owner"] is False
        assert "owner_hash" not in str(page)
        tid = page["tasks"][0]["id"]
        out = client.post(f"/api/share/{sid}/claim", json={"task_id": tid, "name": "Leo"}).json()
        assert out["tasks"][0]["person"] == "Leo"

        act = until(ws, "activity")
        assert "Leo signed up" in act["text"]
        assert until(ws, "plan")["plan"]["tasks"][0]["person"] == "Leo"

        owner = share["owner_url"].split("owner=")[1]
        assert client.get(f"/api/share/{sid}?owner={owner}").json()["owner"] is True
        ws.send_json({"type": "share_delete"})
        assert until(ws, "plan")["plan"]["share"] is None
    assert client.get(f"/api/share/{sid}").status_code == 404


def test_shared_list_stays_after_the_visit_ends_as_agreed():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "table", "action": "add", "row": {"task": "Groceries"}})
        until(ws, "plan")
        ws.send_json({"type": "share_create", "consent": True, "label": ""})
        share = until(ws, "plan")["plan"]["share"]
    sid = share["url"].split("/")[-1]
    assert client.get(f"/api/share/{sid}").status_code == 200
    owner = share["owner_url"].split("owner=")[1]
    assert client.post(f"/api/share/{sid}/delete", json={"owner": owner}).json()["deleted"]


def test_family_page_loads():
    assert client.get("/share/anything").status_code == 200
    assert client.get("/static/share.js").status_code == 200


def _shared_list(ws):
    until(ws, "hello")
    ws.send_json({"type": "table", "action": "add", "row": {"task": "Feed the cat"}})
    until(ws, "plan")
    ws.send_json({"type": "share_create", "consent": True, "label": ""})
    share = until(ws, "plan")["plan"]["share"]
    return share["url"].split("/")[-1], share["owner_url"].split("owner=")[1]


def test_owner_is_told_calmly_when_a_helper_steps_back():
    with client.websocket_connect("/ws") as ws:
        sid, _ = _shared_list(ws)
        tid = client.get(f"/api/share/{sid}").json()["tasks"][0]["id"]
        token = client.post(f"/api/share/{sid}/claim", json={"task_id": tid, "name": "Doris"}).json()["claim_token"]
        until(ws, "plan")
        client.post(f"/api/share/{sid}/unclaim", json={"task_id": tid, "token": token})
        assert until(ws, "plan")["plan"]["tasks"][0]["person"] == ""  # the table updates first
        notice = until(ws, "notice")["text"]
        ws.send_json({"type": "share_delete"})
    assert 'Doris can no longer help with "Feed the cat"' in notice and "open again" in notice
    assert "911" not in notice and "urgent" not in notice.lower()


def test_owner_removing_a_name_is_described_as_their_own_change():
    with client.websocket_connect("/ws") as ws:
        sid, owner = _shared_list(ws)
        tid = client.get(f"/api/share/{sid}").json()["tasks"][0]["id"]
        client.post(f"/api/share/{sid}/claim", json={"task_id": tid, "name": "Doris"})
        until(ws, "plan")
        client.post(f"/api/share/{sid}/unclaim", json={"task_id": tid, "token": owner})
        assert until(ws, "notice")["text"].startswith("You removed Doris")
        ws.send_json({"type": "share_delete"})


def test_shared_page_shows_first_names_only(store):
    rec, _ = store.create("Gloria Mendes", [dict(ROWS[1], person="Andre Silva")])
    page = ShareStore.public(store.get(rec["id"]))
    assert page["label"] == "Gloria" and page["tasks"][0]["person"] == "Andre"
    assert "Mendes" not in str(page) and "Silva" not in str(page)


def test_helper_full_name_is_not_shown_to_other_family():
    with client.websocket_connect("/ws") as ws:
        sid, _ = _shared_list(ws)
        tid = client.get(f"/api/share/{sid}").json()["tasks"][0]["id"]
        out = client.post(f"/api/share/{sid}/claim", json={"task_id": tid, "name": "Doris Chen"}).json()
        page = client.get(f"/api/share/{sid}").json()
        owner_view = until(ws, "plan")["plan"]["tasks"][0]["person"]
        ws.send_json({"type": "share_delete"})
    assert out["tasks"][0]["person"] == "Doris" and page["tasks"][0]["person"] == "Doris"
    assert "Chen" not in str(page)
    assert owner_view == "Doris Chen"  # the owner's own table keeps what was typed


def test_family_page_shows_status_and_stepping_back_clears_confirmed():
    with client.websocket_connect("/ws") as ws:
        sid, _ = _shared_list(ws)
        tid = client.get(f"/api/share/{sid}").json()["tasks"][0]["id"]
        assert client.get(f"/api/share/{sid}").json()["tasks"][0]["status"] == "open"
        token = client.post(f"/api/share/{sid}/claim", json={"task_id": tid, "name": "Doris"}).json()["claim_token"]
        until(ws, "plan")
        ws.send_json({"type": "table", "action": "confirm", "row": {"id": tid, "confirmed": True}})
        assert until(ws, "plan")["plan"]["tasks"][0]["confirmed"] is True
        assert client.get(f"/api/share/{sid}").json()["tasks"][0]["status"] == "confirmed"
        client.post(f"/api/share/{sid}/unclaim", json={"task_id": tid, "token": token})
        after = until(ws, "plan")["plan"]["tasks"][0]
        page = client.get(f"/api/share/{sid}").json()["tasks"][0]
        ws.send_json({"type": "share_delete"})
    assert after["confirmed"] is False and page["status"] == "open"


def test_helper_can_add_their_own_task_to_their_calendar_with_no_account():
    from fastapi.testclient import TestClient
    from web.server import app
    c = TestClient(app)
    import pathlib
    page = (pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "share.html").read_text()
    js = c.get("/static/share.js").text
    assert 'if (mine[t.id]) { const cal = helperCalendarButton(t, who); if (cal) li.appendChild(cal); }' in js   # own task only
    assert "BEGIN:VCALENDAR" in js and 'a.download = "helping.ics"' in js
    assert "if (!day || day < new Date(new Date().toDateString())) return null;" in js   # unreadable or past date: no button
    start = js.index("// ---- \"Add to my calendar\" for a helper's own task")
    block = js[start:js.index("async function api(path, body)")]
    assert "fetch(" not in block                                  # made in the browser, never sent anywhere
    assert 'id="i-calendar"' in page


# ---- Round 13, Part 2: helpers choose what fits their schedule ------------------

TIMED = [{"id": 1, "task": "Drive me home", "date": "2026-11-23", "time": "3 pm", "person": ""},   # a Monday
         {"id": 2, "task": "Pick up groceries", "date": "2026-11-28", "time": "3 pm", "person": ""}]  # a Saturday


def test_an_offer_that_fits_puts_the_name_on_right_away(store):
    from datetime import date
    rec, _ = store.create("Ruth", TIMED)
    rec, token = store.claim(rec["id"], 1, "Leo", "weekdays", "afternoons", "Happy to", today=date(2026, 11, 1))
    row = rec["tasks"][0]
    assert row["person"] == "Leo" and row["offers"][0]["days"] == "weekdays" and row["offers"][0]["note"] == "Happy to"
    assert ShareStore.public(rec)["tasks"][0]["status"] == "helping"


def test_an_offer_that_does_not_fit_waits_for_the_person(store):
    from datetime import date
    rec, _ = store.create("Ruth", TIMED)
    rec, token = store.claim(rec["id"], 2, "Doris", "any", "mornings", today=date(2026, 11, 1))   # task is at 3 pm
    row = rec["tasks"][1]
    assert row["person"] == "" and [o["name"] for o in row["offers"]] == ["Doris"]
    rec, _ = store.claim(rec["id"], 2, "Leo", "weekends", "any", today=date(2026, 11, 1))          # a second offer, and it fits
    assert rec["tasks"][1]["person"] == "Leo" and len(rec["tasks"][1]["offers"]) == 2
    public = ShareStore.public(rec)
    assert "offers" not in str(public) and "hash" not in str(public)      # offers are for the person, not the whole list
    assert store.unclaim(rec["id"], 2, token)["tasks"][1]["offers"][0]["name"] == "Leo"   # Doris can take back her offer


def test_days_and_times_only_take_the_listed_choices(store):
    rec, _ = store.create("Ruth", TIMED)
    rec, _ = store.claim(rec["id"], 1, "Leo", "midnight", "<script>")
    assert (rec["tasks"][0]["offers"][0]["days"], rec["tasks"][0]["offers"][0]["times"]) == ("any", "any")


def test_owner_choosing_someone_who_offered_keeps_their_remove_link(store):
    from datetime import date
    rec, _ = store.create("Ruth", TIMED)
    rec, token = store.claim(rec["id"], 2, "Doris", "any", "mornings", today=date(2026, 11, 1))
    rows = [dict(t) for t in TIMED]; rows[1]["person"] = "Doris"
    rec = store.update_tasks(rec["id"], rows)                            # the person chose Doris anyway
    assert rec["tasks"][1]["person"] == "Doris" and rec["tasks"][1]["claim_hash"]
    assert store.unclaim(rec["id"], 2, token)["tasks"][1]["person"] == ""


def test_signing_up_sends_days_times_and_a_note_and_the_person_sees_the_offer():
    with client.websocket_connect("/ws") as ws:
        until(ws, "hello")
        ws.send_json({"type": "table", "action": "add", "row": {"task": "Pick up groceries", "time": "3 pm"}})
        until(ws, "plan")
        ws.send_json({"type": "share_create", "consent": True, "label": "Ruth"})
        share = until(ws, "plan")["plan"]["share"]
        sid = share["url"].split("/")[-1]
        tid = client.get(f"/api/share/{sid}").json()["tasks"][0]["id"]
        out = client.post(f"/api/share/{sid}/claim", json={"task_id": tid, "name": "Doris", "days": "any",
                                                             "times": "mornings", "note": "Mornings are best"}).json()
        assert out["placed"] is False and out["tasks"][0]["person"] == ""
        assert "Doris offered to help" in until(ws, "activity")["text"]
        task = until(ws, "plan")["plan"]["tasks"][0]
        assert task["offers"] == [{"id": 1, "name": "Doris", "days": "any", "times": "mornings", "note": "Mornings are best"}]
        ws.send_json({"type": "share_delete"})


def test_helper_page_lets_a_helper_pick_tasks_days_times_and_a_note():
    js = client.get("/static/share.js").text
    page = client.get("/share/anything").text
    for words in ('"I can help with this"', '"Which days work for you?"', '"Which times work for you?"',
                  '"A short note (optional)"', '["weekdays", "Weekdays"]', '["mornings", "Mornings"]', '["any", "Any time"]'):
        assert words in js
    assert 'id="signup"' in page and "Sign me up" in page and "Only your first name shows on this list." in page
    assert 'api("/claim", { task_id: id, name, days: c.days, times: c.times, note: c.note.trim() })' in js
    # After signing up: Add to my calendar for each task they're on, and "Let Ruth know" only if she shared her number.
    thanks = js[js.index("function renderThanks"):js.index("function render(data)")]
    assert "helperCalendarButton(t, who)" in thanks and "letThemKnow(" in thanks
    know = js[js.index("function letThemKnow"):js.index("function renderThanks")]
    assert "if (!textBack) return null;" in know and "sms:${textBack}" in know
    assert "location.hash" in js and "fetch(" not in know                  # the number comes from the link, after "#"
