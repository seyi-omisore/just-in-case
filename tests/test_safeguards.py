"""Free, offline tests of every code-level rule. Run: pytest -q

These prove the safeguards without calling the model, so they cost nothing.
"""

from datetime import date
from pathlib import Path

import pytest

from just_in_case.directory import Directory
from just_in_case.safeguards import EMERGENCY_LINE, ESCORT_RULE_LINE, FALLBACK_LINE, check_helper_note
from just_in_case.state import PlanState
from just_in_case.tools import Toolbox, ToolError

TODAY = date(2026, 10, 19)
# Frozen copy of the sample data, so editing data/ never breaks these tests.
FIXTURES = Path(__file__).parent / "fixtures"


ALL_SECTIONS = ["name", "contacts", "medications", "pet", "helpers", "plan"]


CARD_DIR = [None]


@pytest.fixture
def fresh(tmp_path):
    """A brand-new visit: nothing chosen yet."""
    CARD_DIR[0] = tmp_path
    return Toolbox(PlanState(town="Boston"), Directory(FIXTURES, today=TODAY), out_dir=tmp_path, today=TODAY)


@pytest.fixture
def box(fresh):
    """Most tests: the user already ticked every card section."""
    fresh.choose_card_sections(ALL_SECTIONS)
    return fresh


def test_free_services_rank_first(box):
    costs = [s["cost"] for s in box.find_services("ride_home_after_procedure")["services"]]
    assert costs == sorted(costs, key=["free", "low", "paid"].index)


def test_waitlist_and_stale_entries_are_flagged(box):
    found = {s["id"]: s for s in box.find_services("ride_home_after_procedure")["services"]}
    assert any("WAITLIST" in f for f in found["svc_coa_rides"]["flags"])
    assert any("NOT RECENTLY CHECKED" in f for f in found["svc_old_volunteer_drivers"]["flags"])


def test_cannot_plan_unconfirmed_need(box):
    with pytest.raises(ToolError):
        box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")


def test_invented_service_is_rejected(box):
    box.confirm_needs(["ride_home_after_procedure"])
    with pytest.raises(ToolError):
        box.add_to_plan("ride_home_after_procedure", "service", "svc_made_up_by_model")


def test_ride_home_gets_escort_rule_line(box):
    box.confirm_needs(["ride_home_after_procedure"])
    out = box.add_to_plan("ride_home_after_procedure", "service", "svc_escort_volunteer")
    assert ESCORT_RULE_LINE in out["added"]["flags"]


def test_blank_points_to_fallback(box):
    box.confirm_needs(["pet_care_during_stay"])
    out = box.add_to_plan("pet_care_during_stay", "blank")
    assert FALLBACK_LINE in out["added"]["flags"]


def test_people_blocked_by_default_and_in_burden_mode(box):
    box.confirm_needs(["check_in_after_procedure"])
    with pytest.raises(ToolError):  # default: not yet comfortable
        box.add_to_plan("check_in_after_procedure", "person")
    box.set_comfort(True, True)  # comfortable but worried about burden
    with pytest.raises(ToolError):
        box.add_to_plan("check_in_after_procedure", "person")
    with pytest.raises(ToolError):
        box.draft_helper_note(["Marion"], "check in", "Hi! No pressure at all.")


def test_burden_mode_is_sticky(box):
    box.set_comfort(False, True)
    box.set_comfort(True, False)
    assert box.state.people_allowed() is False


@pytest.mark.parametrize("text", [
    "Marion, you must drive me home. No pressure.",
    "Please reply ASAP, I'm counting on you.",
])
def test_pressure_language_rejected(text):
    assert check_helper_note(text) is not None


def test_note_needs_easy_no():
    assert check_helper_note("Hi Marion. Could you check on me after my procedure?") is not None


def test_good_note_passes():
    note = ("Hi Marion. I have a small procedure on the 14th. Would you be up for a quick "
            "phone call that evening to check on me? No pressure at all, and no need to reply.")
    assert check_helper_note(note) is None


def test_helper_cap(box):
    box.set_comfort(True, False)
    with pytest.raises(ToolError):
        box.draft_helper_note(["A", "B", "C", "D"], "visit", "Hi all. No pressure at all.")


def test_card_has_911_and_only_typed_meds(box):
    box.state.user_said.append("I'm Ruth. My friend Marion is 617-555-0142. I use eye drops at night.")
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    out = box.make_fridge_card("Ruth", [{"name": "Marion", "relationship": "friend",
                                         "phone": "617-555-0142"}], ["Eye drops, at night"])
    txt = card_text()
    assert txt.count(EMERGENCY_LINE.upper()) + txt.count(EMERGENCY_LINE) >= 2
    assert "Eye drops, at night" in txt
    assert "Key lockbox" in txt


# ---- Step 1 additions ------------------------------------------------------

def test_missing_phone_is_flagged(box):
    found = box.find_services("help_at_home")["services"]
    entry = next(s for s in found if s["id"] == "svc_no_phone")
    assert entry["phone"] == "see website"
    assert any("NO PHONE LISTED" in f for f in entry["flags"])


def test_usps_hold_mail_is_a_preparation_with_link(box):
    preps = box.find_preparations("mail_while_away")["preparations"]
    usps = next(p for p in preps if p["id"] == "prep_usps_hold_mail")
    assert usps["url"].startswith("https://www.usps.com/")
    box.confirm_needs(["mail_while_away"])
    out = box.add_to_plan("mail_while_away", "preparation", "prep_usps_hold_mail")
    assert out["added"]["label"] == "USPS Hold Mail"


def test_support_lines_include_988_and_wellness_check(box):
    lines = box.get_support_lines()["lines"]
    text = " ".join(l["how_to_reach"] for l in lines)
    assert "988" in text
    assert any(l["kind"] == "wellness_check" for l in lines)
    assert "911" in box.get_support_lines()["emergency"]


def test_support_lines_do_not_touch_the_plan(box):
    box.get_support_lines()
    assert box.state.items == [] and box.state.confirmed_needs == []


def test_card_has_988_line(box):
    from just_in_case.safeguards import CRISIS_LINE
    box.state.user_said.append("I'm Ruth.")
    out = box.make_fridge_card("Ruth", [], [])
    txt = card_text()
    assert CRISIS_LINE in txt


def test_card_shows_service_phone(box):
    box.confirm_needs(["ride_home_after_procedure"])
    box.add_to_plan("ride_home_after_procedure", "service", "svc_escort_volunteer")
    box.state.user_said.append("I'm Ruth.")
    out = box.make_fridge_card("Ruth", [], [])
    txt = card_text()
    assert "SAMPLE Volunteer Medical Escort Program, TO BE VERIFIED" in txt



# ---- No assumptions on the card --------------------------------------------

def card_text(out=None):
    import glob
    return open(sorted(glob.glob(str(CARD_DIR[0] / "*.txt")))[-1]).read()


def test_card_blocks_details_the_user_never_said(box):
    box.state.user_said.append("I'm Ruth and I live alone.")
    with pytest.raises(ToolError, match="Not said by the user"):
        box.make_fridge_card("Ruth", [], [], "Fifi (cat)", "")
    with pytest.raises(ToolError, match="medication"):
        box.make_fridge_card("Ruth", [], ["Lisinopril 10mg"])
    with pytest.raises(ToolError, match="phone"):
        box.make_fridge_card("Ruth", [{"name": "Ruth", "relationship": "", "phone": "617-555-0199"}], [])


def test_card_leaves_out_sections_the_user_did_not_give(box):
    box.state.user_said.append("I'm Ruth.")
    txt = card_text(box.make_fridge_card("Ruth", [], []))
    assert "MEDICATIONS" not in txt and "MY PET" not in txt and "PEOPLE TO CONTACT" not in txt
    assert "none listed" not in txt


def test_card_accepts_the_users_own_words(box):
    box.state.user_said += ["My cat Max. Doris Chen 617-555-0177 feeds him.",
                            "I take a blood pressure pill in the morning."]
    txt = card_text(box.make_fridge_card("", [{"name": "Doris Chen", "relationship": "neighbor",
                                               "phone": "(617) 555-0177"}],
                                         ["Blood pressure pill, mornings"], "Max (cat)", "Doris Chen"))
    assert "Max (cat), looked after by Doris Chen" in txt
    assert "Blood pressure pill, mornings" in txt


def test_asking_for_a_note_by_name_is_enough(fresh):
    """Regression: 'draft a note to my friend Samantha' was wrongly refused."""
    fresh.state.user_said.append("draft a note to my friend Samantha")
    assert fresh.precheck("draft_helper_note", {"helper_names": ["Samantha"],
                          "note_text": "Hi Samantha. Could you feed Fifi? No pressure at all."}) is None
    fresh.draft_helper_note(["Samantha"], "feed Fifi", "Hi Samantha. Could you feed Fifi? No pressure at all.")
    assert fresh.state.people_allowed()


def test_agent_cannot_bring_up_people_on_its_own_in_burden_mode(fresh):
    fresh.state.user_said += ["I could ask Samantha maybe.", "Actually I don't want to bother anyone."]
    fresh.set_comfort(False, True)
    assert fresh.precheck("draft_helper_note", {"helper_names": ["Samantha"],
                          "note_text": "Hi Samantha. No pressure at all."})


def test_rules_are_checked_before_the_user_is_asked(fresh):
    fresh.state.user_said.append("I'm Ruth.")
    assert "choose_card_sections" in fresh.precheck("make_fridge_card", {"name": "Ruth"})
    fresh.choose_card_sections(["contacts"])
    assert "did not pick" in fresh.precheck("make_fridge_card", {"name": "Ruth"})
    assert fresh.precheck("make_fridge_card", {"name": ""}) is None


def test_card_result_has_no_file_path(box):
    box.state.user_said.append("I'm Ruth.")
    out = box.make_fridge_card("Ruth", [], [])
    assert "/" not in out["tell_the_user"] and "See your fridge card" in out["tell_the_user"]


def test_note_only_to_people_the_user_named(box):
    box.set_comfort(True, False)
    box.state.user_said.append("I could ask Samantha.")
    with pytest.raises(ToolError, match="has not named"):
        box.draft_helper_note(["Marion"], "feed the cat", "Hi Marion. Could you feed my cat? No pressure at all.")
    box.draft_helper_note(["Samantha"], "feed the cat", "Hi Samantha. Could you feed my cat? No pressure at all.")


# ---- Helper table ------------------------------------------------------------

def test_agent_adds_helper_row_only_for_named_people(box):
    box.set_comfort(True, False)
    box.state.user_said.append("Samantha could feed the cat on the 14th.")
    with pytest.raises(ToolError, match="has not named"):
        box.add_helper_task("Feed the cat", "Marion")
    open_row = box.add_helper_task("Pick up groceries")["added"]
    assert open_row["person"] == ""
    row = box.add_helper_task("Feed the cat", "Samantha", "Nov 14", "Evening")["added"]
    assert row["person"] == "Samantha" and row["id"] == 2


def test_no_helper_rows_in_burden_mode(box):
    box.set_comfort(True, True)
    box.state.user_said.append("Samantha")
    with pytest.raises(ToolError, match="not said they would like help"):
        box.add_helper_task("Feed the cat", "Samantha")


def test_user_can_add_edit_and_remove_rows(box):
    box.user_edit_task("add", {"task": "Drive me home", "person": "Leo", "date": "Nov 14"})
    rid = box.state.helper_tasks[0]["id"]
    box.user_edit_task("update", {"id": rid, "task": "Drive me home", "person": "Leo", "time": "2 pm"})
    assert box.state.helper_tasks[0]["time"] == "2 pm"
    box.user_edit_task("delete", {"id": rid})
    assert box.state.helper_tasks == []
    with pytest.raises(ToolError):
        box.user_edit_task("add", {"task": "", "person": "Leo"})


def test_spreadsheet_download_cannot_run_formulas(box):
    box.user_edit_task("add", {"task": "=HYPERLINK(\"http://bad\")", "person": "+Leo"})
    csv_text = box.tasks_csv()
    assert csv_text.splitlines()[0] == "Task,Date,Time,Who is helping,Status"
    assert "'=HYPERLINK" in csv_text and "'+Leo" in csv_text


def test_card_lists_who_is_helping(box):
    box.state.user_said.append("I'm Ruth.")
    box.user_edit_task("add", {"task": "Feed the cat", "person": "Samantha", "date": "Nov 14"})
    assert "Feed the cat (Nov 14): Samantha" in card_text(box.make_fridge_card("Ruth", [], []))


def test_card_refreshes_when_table_changes(box):
    box.state.user_said.append("I'm Ruth.")
    out = box.make_fridge_card("Ruth", [], [])
    box.user_edit_task("add", {"task": "Drive me home", "person": "Leo"})
    assert "Drive me home: Leo" in card_text(out)
    box.user_edit_task("delete", {"id": box.state.helper_tasks[0]["id"]})
    assert "Leo" not in card_text(out)


def test_card_names_the_chosen_helper(box):
    box.set_comfort(True, False)
    box.state.user_said.append("I'm Ruth. I could ask Tasha.")
    box.confirm_needs(["pet_care_during_stay"])
    box.add_to_plan("pet_care_during_stay", "person")
    box.draft_helper_note(["Tasha"], "feed my dog", "Hi Tasha. Could you feed my dog? No pressure at all.")
    assert "Care for a pet during a hospital stay: Tasha" in card_text(box.make_fridge_card("Ruth", [], []))


def test_open_task_without_a_name(box):
    box.state.user_said.append("I'm Ruth.")
    box.user_edit_task("add", {"task": "Rides to physical therapy", "date": "Tuesdays"})
    assert box.state.helper_tasks[0]["person"] == ""
    assert "Rides to physical therapy (Tuesdays): open, anyone can sign up" in card_text(
        box.make_fridge_card("Ruth", [], []))


# ---- Card checklist -------------------------------------------------------------

def test_card_needs_the_checklist_first(fresh):
    fresh.state.user_said.append("I'm Ruth.")
    with pytest.raises(ToolError, match="choose_card_sections"):
        fresh.make_fridge_card("Ruth", [], [])


def test_card_blocks_sections_the_user_did_not_tick(fresh):
    fresh.state.user_said.append("I'm Ruth. Marion 617-555-0142. Eye drops at night.")
    fresh.choose_card_sections(["contacts"])
    with pytest.raises(ToolError, match="did not pick"):
        fresh.make_fridge_card("Ruth", [], ["Eye drops at night"])
    with pytest.raises(ToolError, match="did not pick"):
        fresh.make_fridge_card("Ruth", [], [])  # name was not ticked either
    txt = card_text(fresh.make_fridge_card(
        "", [{"name": "Marion", "relationship": "", "phone": "617-555-0142"}], []))
    assert "Marion" in txt and "MEDICATIONS" not in txt


def test_ticking_nothing_gives_just_the_emergency_lines(fresh):
    fresh.state.user_said.append("I'm Ruth.")
    fresh.confirm_needs(["spare_key_access"])
    fresh.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    fresh.user_edit_task("add", {"task": "Rides"})
    fresh.choose_card_sections([])
    txt = card_text(fresh.make_fridge_card("", [], []))
    assert "911" in txt and "988" in txt
    assert "MY PLAN" not in txt and "WHO IS HELPING" not in txt


def test_unknown_section_rejected(fresh):
    with pytest.raises(ToolError):
        fresh.choose_card_sections(["social_security_number"])


# ---- "Why this matters" tips ------------------------------------------------

def test_every_need_has_a_plain_why_tip():
    from just_in_case.safeguards import reading_grade
    from just_in_case.state import NEED_WHY, NEEDS
    assert set(NEED_WHY) == set(NEEDS)
    for need, text in NEED_WHY.items():
        assert reading_grade(text) <= 8, f"{need}: grade {reading_grade(text):.1f}"


# ---- Allergies and important papers on the card ----------------------------

def test_card_shows_allergies_and_papers_in_the_users_words(fresh):
    fresh.state.user_said += ["I'm allergic to penicillin and shellfish.",
                              "My papers are in the blue folder in my desk drawer."]
    fresh.choose_card_sections(["allergies", "papers"])
    txt = card_text(fresh.make_fridge_card("", [], [], allergies=["Penicillin", "Shellfish"],
                                           papers="Blue folder, desk drawer"))
    assert "MY ALLERGIES" in txt and "Penicillin" in txt and "Shellfish" in txt
    assert "WHERE MY IMPORTANT PAPERS ARE" in txt and "Blue folder, desk drawer" in txt


def test_card_blocks_allergies_and_papers_the_user_never_said(fresh):
    fresh.state.user_said.append("I'm allergic to penicillin.")
    fresh.choose_card_sections(["allergies", "papers"])
    with pytest.raises(ToolError, match="allergy"):
        fresh.make_fridge_card("", [], [], allergies=["Peanuts"])
    with pytest.raises(ToolError, match="papers"):
        fresh.make_fridge_card("", [], [], papers="In the safe in the closet")
    assert "allergy" in fresh.precheck("make_fridge_card", {"name": "", "allergies": ["Latex"]})


def test_card_blocks_allergies_and_papers_the_user_did_not_tick(fresh):
    fresh.state.user_said.append("Penicillin. My papers are in the desk.")
    fresh.choose_card_sections(["contacts"])
    with pytest.raises(ToolError, match="did not pick"):
        fresh.make_fridge_card("", [], [], allergies=["Penicillin"])
    with pytest.raises(ToolError, match="did not pick"):
        fresh.make_fridge_card("", [], [], papers="In the desk")


def test_card_sections_have_icons_next_to_their_titles(fresh):
    import glob
    fresh.state.user_said.append("Penicillin.")
    fresh.choose_card_sections(["allergies"])
    fresh.make_fridge_card("", [], [], allergies=["Penicillin"])
    page = open(sorted(glob.glob(str(CARD_DIR[0] / "*.html")))[-1]).read()
    assert '<h2><svg class="sec-icon"' in page and "My allergies, in my own words</h2>" in page


# ---- No one to ask: a services-only plan -------------------------------------

def test_no_one_to_ask_gives_a_services_only_plan(box):
    out = box.set_comfort(True, False, no_one_to_ask=True)
    assert box.state.people_allowed() is False and box.state.services_only()
    assert "617-635-4366" in out["tell_the_user"] and "2-1-1" in out["tell_the_user"]
    assert "Council on Aging" in out["tell_the_user"]
    box.confirm_needs(["pet_care_during_stay"])
    with pytest.raises(ToolError, match="People are not part"):
        box.add_to_plan("pet_care_during_stay", "person")
    assert "has not said" in box.precheck("add_helper_task", {"task": "Feed the cat"})


def test_naming_someone_later_ends_services_only(fresh):
    fresh.set_comfort(False, False, no_one_to_ask=True)
    fresh.state.user_said.append("Actually, could you write a note to my neighbor Tasha?")
    fresh.draft_helper_note(["Tasha"], "feed the cat",
                            "Hi Tasha. Could you feed my cat one day? No pressure at all.")
    assert fresh.state.people_allowed() and not fresh.state.services_only()


# ---- Save and continue later --------------------------------------------------

def _new_box(tmp_path):
    return Toolbox(PlanState(town="Boston"), Directory(FIXTURES, today=TODAY), out_dir=tmp_path, today=TODAY)


def test_saved_plan_round_trip(box, tmp_path):
    box.state.user_said += ["I'm Ruth. I'd like to ask Tasha. Tasha 617-555-0142."]
    box.set_comfort(True, False)
    box.confirm_needs(["ride_home_after_procedure", "pet_care_during_stay"])
    ride = box.find_services("ride_home_after_procedure")["services"][0]["id"]
    box.add_to_plan("ride_home_after_procedure", "service", ride)
    box.add_to_plan("pet_care_during_stay", "person")
    box.draft_helper_note(["Tasha"], "feed the cat", "Hi Tasha. Could you feed my cat? No pressure at all.")
    box.user_edit_task("add", {"task": "Feed the cat", "person": "Tasha"})
    box.make_fridge_card("Ruth", [{"name": "Tasha", "relationship": "", "phone": "617-555-0142"}], [])
    again = _new_box(tmp_path / "again")
    assert again.restore(box.export()) == []
    assert [(i.need, i.route) for i in again.state.items] == [(i.need, i.route) for i in box.state.items]
    assert again.state.helper_tasks[0]["person"] == "Tasha" and again.state.card_path


def test_tampered_save_cannot_get_around_the_rules(fresh, tmp_path):
    saved = {"user_said": ["I have a procedure."], "needs": ["ride_home_after_procedure"],
             "comfort": [False, False, False],
             "items": [{"need": "ride_home_after_procedure", "route": "service", "ref_id": "svc_made_up"},
                       {"need": "ride_home_after_procedure", "route": "person", "ref_id": None}],
             "notes": [{"helpers": ["Gloria"], "role": "", "text": "You must drive me. No pressure."}],
             "sections": ["medications"],
             "card": {"name": "", "contacts": [], "medications": ["Lisinopril 10mg"]}}
    skipped = fresh.restore(saved)
    assert fresh.state.items == [] and fresh.state.helper_notes == [] and not fresh.state.card_path
    assert "a plan item" in skipped and "a note" in skipped and "the card" in skipped


def test_restore_ignores_junk(fresh):
    assert isinstance(fresh.restore({"needs": "not a list", "items": [42], "comfort": "yes"}), list)
    assert fresh.state.items == []


# ---- Planning for someone else ------------------------------------------------

def test_no_planning_until_the_person_agrees(fresh):
    fresh.state.user_said.append("I'm helping my mom, Ana, make a plan.")
    out = fresh.confirm_helping_someone("Ana", agreed=False)
    assert out["agreed"] is False and "talk with Ana first" in out["tell_the_user"]
    with pytest.raises(ToolError, match="has not agreed"):
        fresh.confirm_needs(["power_outage"])
    assert "has not agreed" in fresh.precheck("confirm_needs", {"needs": ["power_outage"]})
    fresh.confirm_helping_someone("Ana", agreed=True)
    assert fresh.confirm_needs(["power_outage"])["confirmed_needs"] == ["power_outage"]


def test_add_to_plan_is_blocked_while_waiting_for_agreement(fresh):
    fresh.confirm_needs(["power_outage"])
    fresh.confirm_helping_someone("", agreed=False)
    with pytest.raises(ToolError, match="has not agreed"):
        fresh.add_to_plan("power_outage", "blank")


def test_agreement_is_the_users_tap_not_the_agents_input():
    import asyncio
    from just_in_case.agent import make_gate

    async def says_not_yet(short, data):
        return False
    gate = make_gate(says_not_yet, trace=lambda *_: None)
    decision = asyncio.run(gate("mcp__jic__confirm_helping_someone", {"person_name": "", "agreed": True}, None))
    assert decision.updated_input["agreed"] is False


def test_names_the_user_never_said_are_dropped(fresh):
    fresh.state.user_said.append("I'm helping my mom.")
    fresh.confirm_helping_someone("Margaret", agreed=True)
    assert fresh.state.planning_for == ""


# ---- Progress counts only what the user said yes to ---------------------------

def test_items_start_unconfirmed_and_no_thanks_removes_them(box):
    box.confirm_needs(["spare_key_access", "power_outage"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.add_to_plan("power_outage", "blank")
    assert [i.user_ok for i in box.state.items] == [False, False]
    box.user_confirm_item("spare_key_access", True)
    assert box.state.items[0].user_ok is True
    with pytest.raises(ToolError):
        box.user_confirm_item("power_outage", True)  # "Not covered yet" is not something to accept
    box.user_confirm_item("spare_key_access", False)
    assert [i.need for i in box.state.items] == ["power_outage"]


def test_replacing_an_item_asks_again(box):
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.user_confirm_item("spare_key_access", True)
    box.add_to_plan("spare_key_access", "blank")
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    assert box.state.items[0].user_ok is False


def test_saved_yes_answers_come_back(box, tmp_path):
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.user_confirm_item("spare_key_access", True)
    again = _new_box(tmp_path / "again")
    again.restore(box.export())
    assert again.state.items[0].user_ok is True


# ---- Getting ready timeline -----------------------------------------------------

def test_timeline_uses_the_users_date_and_only_confirmed_parts(box):
    from just_in_case import timeline
    box.confirm_needs(["ride_home_after_procedure", "spare_key_access"])
    ride = box.find_services("ride_home_after_procedure")["services"][0]["id"]
    box.add_to_plan("ride_home_after_procedure", "service", ride)
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    assert timeline.build(box.state, TODAY)["steps"] == []  # nothing said yes to yet
    box.user_confirm_item("spare_key_access", True)
    tl = timeline.build(box.state, TODAY)
    assert tl["date"] == "" and all(s["date"] == "" for s in tl["steps"])  # no date until the user picks one
    assert any("lockbox" in s["text"] for s in tl["steps"]) and not any("ride" in s["text"] for s in tl["steps"])
    box.user_set_event_date("2026-11-02")
    tl = timeline.build(box.state, TODAY)
    assert tl["countdown"] == "14 days to go." and tl["label"] == "procedure"
    key = next(s for s in tl["steps"] if s["text"] == "Put a key in the lockbox.")
    assert key["date"] == "2026-11-01" and key["when"] == "The day before"
    assert tl["steps"][-1]["when"] == "The morning of"  # in order, ending on the day


def test_timeline_date_must_be_a_real_date_in_the_next_year(box):
    with pytest.raises(ToolError):
        box.user_set_event_date("next month")
    with pytest.raises(ToolError):
        box.user_set_event_date("2030-01-01")
    box.user_set_event_date("")  # clearing is fine


def test_ticking_a_step_and_countdown_words(box):
    from datetime import date
    from just_in_case import timeline
    box.confirm_needs(["mail_while_away"])
    box.add_to_plan("mail_while_away", "preparation", "prep_usps_hold_mail")
    box.user_confirm_item("mail_while_away", True)
    box.user_set_event_date("2026-10-20")
    step = timeline.build(box.state, TODAY)["steps"][0]
    box.user_tick_step(step["id"], True)
    assert timeline.build(box.state, TODAY)["steps"][0]["done"] is True
    with pytest.raises(ToolError):
        box.user_tick_step("made:up", True)
    assert timeline.countdown(date(2026, 10, 19), TODAY) == "Today is the day."
    assert timeline.countdown(date(2026, 10, 20), TODAY) == "Tomorrow."
    assert "passed" in timeline.countdown(date(2026, 10, 1), TODAY)


def test_timeline_wording_is_plain(box):
    from just_in_case import timeline
    from just_in_case.safeguards import reading_grade
    texts = [t for steps in timeline.STEPS.values() for _, t in steps] + [t for _, t in timeline.ALWAYS]
    assert all(reading_grade(t) <= 8 for t in texts), [t for t in texts if reading_grade(t) > 8]


# ---- Helper task status ----------------------------------------------------------

def test_task_status_open_helping_confirmed(box):
    from just_in_case.tools import task_status
    box.user_edit_task("add", {"task": "Feed the cat"})
    t = box.state.helper_tasks[0]
    assert task_status(t) == "open"
    with pytest.raises(ToolError, match="Add who is helping"):
        box.user_edit_task("confirm", {"id": t["id"], "confirmed": True})
    box.user_edit_task("update", {"id": t["id"], "task": "Feed the cat", "person": "Doris"})
    assert task_status(t) == "helping"
    box.user_edit_task("confirm", {"id": t["id"], "confirmed": True})
    assert task_status(t) == "confirmed"
    assert box.tasks_csv().splitlines()[1].endswith(",Doris,Confirmed")
    box.user_edit_task("update", {"id": t["id"], "task": "Feed the cat", "person": "Marion"})
    assert task_status(t) == "helping"  # a confirmation never moves to a new name


# ---- "Your next step" card ----------------------------------------------------

def _item(need, route="service", name="FriendshipWorks Medical Escort", phone="617-482-1510", url="", ok=True):
    return {"need": need, "route": route, "name": name, "phone": phone, "url": url, "user_ok": ok}


def test_next_step_is_the_most_important_accepted_part():
    from just_in_case.next_step import pick_next_step
    items = [_item("mail_while_away", "preparation", "USPS Hold Mail", "", "https://www.usps.com"),
             _item("ride_home_after_procedure")]
    step = pick_next_step(items, set(), [])
    assert step["text"] == "Call FriendshipWorks Medical Escort to book your ride home."
    assert step["phone"] == "617-482-1510" and step["left"] == 1


def test_next_step_ignores_parts_the_user_has_not_said_yes_to():
    from just_in_case.next_step import pick_next_step
    waiting = [_item("ride_home_after_procedure", ok=False), _item("spare_key_access", "blank", ok=True)]
    assert pick_next_step(waiting, set(), []) is None  # no step until a part is accepted


def test_done_shows_the_next_step_and_later_goes_to_the_back():
    from just_in_case.next_step import pick_next_step
    items = [_item("ride_home_after_procedure"), _item("pet_care_during_stay", name="MSPCA-Angell", phone="", url="https://www.mspca.org")]
    assert pick_next_step(items, {"ride_home_after_procedure"}, [])["need"] == "pet_care_during_stay"
    assert pick_next_step(items, set(), ["ride_home_after_procedure"])["need"] == "pet_care_during_stay"
    only = pick_next_step(items[:1], set(), ["ride_home_after_procedure"])  # nothing else: it comes back
    assert only["need"] == "ride_home_after_procedure"
    assert pick_next_step(items, {"ride_home_after_procedure", "pet_care_during_stay"}, []) == {"all_done": True}


def test_next_step_words_fit_what_we_know():
    from just_in_case.next_step import step_text
    assert step_text(_item("pet_care_during_stay", name="MSPCA-Angell", phone="", url="https://x.org")) == \
        "Visit the MSPCA-Angell website to plan care for your pet."
    assert step_text(_item("transportation", name="City of Boston Age Strong Commission (Boston's Council on Aging)")) == \
        "Call City of Boston Age Strong Commission to set up your rides."
    assert step_text(_item("spare_key_access", "person", "Doris", "")) == \
        "Ask Doris if they can help you set up a spare key."


def test_user_next_step_needs_an_accepted_part_and_survives_save_and_restore(box):
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    with pytest.raises(ToolError):
        box.user_next_step("spare_key_access", "done")  # not accepted yet
    box.user_confirm_item("spare_key_access", True)
    box.user_next_step("spare_key_access", "later")
    assert box.state.actions_later == ["spare_key_access"]
    box.user_next_step("spare_key_access", "done")
    assert box.state.actions_done == {"spare_key_access"} and box.state.actions_later == []
    with pytest.raises(ToolError):
        box.user_next_step("spare_key_access", "snooze")  # only done or later
    saved = box.export()
    assert saved["actions_done"] == ["spare_key_access"]


# ---- Encouragement at milestones -----------------------------------------------

def test_encouragement_names_what_the_person_did_in_plain_words():
    from just_in_case.encouragement import encouragement
    assert encouragement(0, 4, False) is None and encouragement(0, 0, False) is None
    assert encouragement(1, 4, False) == "You've planned for 1 of 4 situations. A good start."
    assert encouragement(3, 4, False) == "You've planned for 3 of 4 situations. Nice work."
    assert encouragement(4, 4, True) == "Your plan is ready. You can print your card at any time."
    # No card yet: do not promise a card that does not exist.
    assert "fridge card when you'd like" in encouragement(2, 2, False)


def test_encouragement_never_gushes_or_talks_down():
    from just_in_case.encouragement import encouragement
    from just_in_case.safeguards import reading_grade
    lines = [encouragement(c, t, card) for t in range(1, 11) for c in range(0, t + 1) for card in (True, False)]
    lines = [l for l in lines if l]
    banned = ("great job", "good job", "awesome", "amazing", "proud of you", "well done", "you did it", "sweetie", "dear")
    for line in lines:
        assert "!" not in line, line
        assert not any(b in line.lower() for b in banned), line
        assert len(line.split(". ")) <= 3 and reading_grade(line) <= 7, line


# ---- Privacy: the AI toolkit must not leave a copy of the chat behind ----------

def test_agent_keeps_its_conversation_log_in_a_private_folder_that_is_deleted(monkeypatch):
    import asyncio
    import os
    from pathlib import Path
    import just_in_case.agent as agent_mod
    monkeypatch.setattr(agent_mod, "runtime_env", lambda: {"ANTHROPIC_AUTH_TOKEN": "test"})
    agent = agent_mod.JustInCaseAgent(trace=lambda *_: None)
    folder = Path(agent.options.env["CLAUDE_CONFIG_DIR"])
    assert folder.is_dir() and folder != Path.home() / ".claude"   # never the shared ~/.claude folder
    assert str(folder).startswith(os.path.realpath(__import__("tempfile").gettempdir())) or "jic-private-" in folder.name

    class Client:  # stands in for the SDK client
        async def disconnect(self):
            pass
    agent.client = Client()
    asyncio.run(agent.__aexit__(None, None, None))
    assert not folder.exists()  # gone when the visit ends


# ---- "Mark my plan as reviewed" ----------------------------------------------

def _plan_with_one_accepted_part(box):
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.user_confirm_item("spare_key_access", True)


def test_review_needs_something_in_the_plan_and_records_only_the_date(box):
    with pytest.raises(ToolError):
        box.user_review("mark")                      # nothing to review yet
    _plan_with_one_accepted_part(box)
    box.user_review("mark")
    box.user_review("mark")                          # a second tap the same day adds nothing
    assert box.state.reviews == [box.today.isoformat()]
    with pytest.raises(ToolError):
        box.user_review("remind")                    # only mark or undo
    box.user_review("undo")
    assert box.state.reviews == []
    with pytest.raises(ToolError):
        box.user_review("undo")


def test_review_suggestion_is_gentle_and_comes_after_a_few_months():
    from datetime import date, timedelta
    from just_in_case.reviews import review_status
    today = date(2026, 10, 24)
    assert review_status([], today, has_plan=False) is None
    first = review_status([], today, has_plan=True)
    assert "when you are ready" in first["text"].lower() and first["count"] == 0
    recent = review_status(["2026-10-24"], today, True)
    assert recent["reviewed_today"] is True and "October 24, 2026" in recent["text"]
    old = review_status([(today - timedelta(days=130)).isoformat()], today, True)
    assert "few months" in old["text"] and "If you would like" in old["text"]
    for status in (first, recent, old):
        text = status["text"].lower()
        assert "!" not in text
        assert not any(w in text for w in ("overdue", "late", "must", "urgent", "don't forget", "warning"))


def test_review_dates_show_on_the_plan_print_and_the_card_and_survive_saving(box):
    from datetime import timedelta
    from just_in_case.card import render_plan_html
    _plan_with_one_accepted_part(box)
    box.user_review("mark")
    page = render_plan_html(box.state, box.dir, box.today)
    assert "Plan last reviewed:" in page
    by = (box.today + timedelta(days=30 * 4)).strftime("%B %Y")
    assert f"Review this plan by: {by}" in page      # counted from the review, not from the card
    saved = box.export()
    assert saved["reviews"] == [box.today.isoformat()]


def test_restoring_ignores_review_dates_that_are_not_real(box):
    box.restore({"reviews": ["2026-01-05", "not a date", "2999-01-01", 5, None]})
    assert box.state.reviews == ["2026-01-05"] or box.state.reviews == []  # only a real, past date can stay
    assert "2999-01-01" not in box.state.reviews and "not a date" not in box.state.reviews


# ---- "My readiness summary" ----------------------------------------------------

def test_readiness_counts_only_what_the_person_said_yes_to(box):
    from just_in_case.summary import readiness
    box.confirm_needs(["spare_key_access", "power_outage"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.add_to_plan("power_outage", "service", "svc_utility_medical")
    d = readiness(box.state, box.dir, box.today)
    assert d["covered"] == 0 and d["total"] == 2          # nothing yet: no yes from the person
    assert all(s["supports"][0]["kind"] == "waiting" for s in d["situations"])
    box.user_confirm_item("spare_key_access", True)
    d = readiness(box.state, box.dir, box.today)
    assert d["covered"] == 1 and d["across"]["key_lockbox"] is True and d["across"]["fridge_card"] is False
    key = next(s for s in d["situations"] if s["need"] == "spare_key_access")
    assert key["supports"][0]["label"] == "Ready: Key lockbox"


def test_readiness_dates_and_review_count_come_from_the_plan(box):
    from just_in_case.summary import readiness
    box.confirm_needs(["spare_key_access"])
    assert box.state.started == box.today.isoformat()      # the day the plan began
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.user_confirm_item("spare_key_access", True)
    box.user_review("mark")
    d = readiness(box.state, box.dir, box.today)
    assert d["review_count"] == 1 and d["last_review"] == d["started"] == d["created"]
    box.confirm_needs(["spare_key_access", "power_outage"])  # asking again never moves the start day
    assert box.state.started == box.today.isoformat()


def test_start_day_survives_saving_but_never_a_future_date(box):
    saved = {"started": "2026-01-05", "needs": ["spare_key_access"]}
    box.restore(saved)
    assert box.state.started == "2026-01-05"
    other = box.__class__(type(box.state)(), box.dir, box.out_dir)
    other.restore({"started": "2999-01-01", "needs": ["spare_key_access"]})
    assert other.state.started == other.today.isoformat()


def test_summary_page_is_built_from_the_plan_with_icons_and_short_labels(box):
    from just_in_case.summary import render_summary_html
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.user_confirm_item("spare_key_access", True)
    page = render_summary_html(box.state, box.dir, box.today)
    for words in ("My readiness summary", "Started:", "Last reviewed:", "Not yet", "Times reviewed:",
                  "Key lockbox", "Fridge card:", "Not made yet", "In an emergency, call 911."):
        assert words in page
    assert "<svg" in page and 'aria-hidden="true"' in page


def test_summary_lets_the_person_choose_parts_and_hides_the_choosing_when_printed(box):
    from just_in_case.summary import render_summary_html
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    box.user_confirm_item("spare_key_access", True)
    page = render_summary_html(box.state, box.dir, box.today)
    for part in ("dates", "situations", "helpers", "lockbox", "card"):
        assert f'<input type="checkbox" value="{part}" checked>' in page   # all start ticked
        assert f'data-part="{part}"' in page                               # and each one names a real part
    assert "Choose what to include" in page and "Save as PDF" in page
    assert "@media print" in page and ".print, .choose { display: none; }" in page.replace("{{", "{").replace("}}", "}")
    assert "In an emergency, call 911." in page.split("<form")[0] .upper().title() or "IN AN EMERGENCY, CALL 911." in page
    assert "fetch(" not in page and "XMLHttpRequest" not in page         # choosing never sends anything


def test_summary_says_clearly_at_the_top_that_it_is_self_reported_and_not_an_assessment(box):
    from just_in_case.summary import SELF_REPORT_NOTE, render_summary_html
    page = render_summary_html(box.state, box.dir, box.today)
    assert SELF_REPORT_NOTE == "This is a self reported summary of my planning. It is not a medical or functional assessment."
    top = page[:page.index("Choose what to include")]
    assert SELF_REPORT_NOTE in top and 'class="note"' in top          # above the tick boxes and every section
    assert page.index(SELF_REPORT_NOTE) < page.index("Made on")
    assert 'value="note"' not in page                                  # it is never an option to leave off
    assert ".choose { display: none; }" not in page.split(".note")[1].split("}")[0]  # and the print rules never hide it


def test_summary_shows_helpers_by_first_name_only_like_the_shared_list(box):
    from just_in_case.summary import render_summary_html
    from just_in_case.safeguards import first_name
    from web.share_store import first_name as shared_first
    assert first_name("Doris Chen") == shared_first("Doris Chen") == "Doris"   # one rule for both
    box.confirm_needs(["spare_key_access"])
    box.set_comfort(True, False)
    box.add_to_plan("spare_key_access", "person", None)
    box.user_confirm_item("spare_key_access", True)
    box.state.helper_notes = [{"helpers": ["Doris Chen"], "role": "key", "text": "x"}]
    page = render_summary_html(box.state, box.dir, box.today)
    assert "Helper chosen: Doris" in page and "Chen" not in page


def test_nothing_about_reviews_or_the_summary_goes_into_a_shared_list(tmp_path):
    from web.share_store import ShareStore
    store = ShareStore(tmp_path)
    rec, _ = store.create("Gloria", [{"id": 1, "task": "Rides", "date": "", "time": "", "person": ""}])
    assert set(rec) == {"id", "owner_hash", "label", "created", "expires", "tasks"}   # only the helper list
    text = (tmp_path / f"{rec['id']}.json").read_text().lower()
    assert "review" not in text and "summary" not in text and "started" not in text


# ---- No tracking, no scoring, no judging ---------------------------------------

def test_agent_is_told_never_to_judge_score_or_track_the_person():
    from just_in_case.prompts import SYSTEM_PROMPT
    text = " ".join(SYSTEM_PROMPT.split()).lower()
    for rule in ("never judge how well the person is doing or whether they can live alone",
                 "do not score, rate, or comment on their health, memory, or independence",
                 "track or mention how long they use the app or how often they come back"):
        assert rule in text


def test_the_agent_has_only_these_tools_and_none_can_track_score_or_assess():
    import inspect
    import just_in_case.tools as tools_mod
    names = set(__import__("re").findall(r'@tool\("([a-z_]+)"', inspect.getsource(tools_mod)))
    assert names == {"confirm_helping_someone", "confirm_needs", "set_comfort", "find_services", "find_preparations",
                     "add_to_plan", "draft_helper_note", "add_helper_task", "choose_card_sections",
                     "make_fridge_card", "get_support_lines", "review_plan"}   # a new tool must be added here on purpose


def test_the_plan_keeps_no_visit_timing_login_counts_or_scores(box):
    import dataclasses
    import re
    from just_in_case.state import PlanState
    banned = re.compile(r"login|log_in|logged|last_seen|last_visit|visit|session_time|time_spent|duration|elapsed|"
                        r"score|rating|grade_of|cognit|memory_|capacity|ability|independen|risk", re.I)
    fields = [f.name for f in dataclasses.fields(PlanState)]
    assert not [f for f in fields if banned.search(f)], fields
    saved = box.export()
    assert not [k for k in saved if banned.search(k)], list(saved)       # nothing like it in a saved plan either


def test_no_code_measures_time_in_the_app_or_counts_visits():
    import pathlib
    import re
    pattern = re.compile(r"time_spent|session_length|visit_count|login_count|last_seen|performance\.now|setInterval\([^)]*(count|track)|"
                         r"(health|memory|cognitive|independence)_?score", re.I)
    root = pathlib.Path(__file__).resolve().parent.parent
    files = [*root.glob("just_in_case/*.py"), *root.glob("web/*.py"), *root.glob("web/static/*.js")]
    hits = [f"{f.name}: {m.group(0)}" for f in files for m in [pattern.search(f.read_text())] if m]
    assert not hits, hits


def test_an_outside_service_is_always_named_as_separate_with_its_own_privacy():
    from just_in_case.safeguards import pressure_phrases, separate_service_line
    line = separate_service_line("CaringBridge")
    assert line == ("CaringBridge is a separate service with its own privacy settings. "
                    "Just in Case never makes an account or shares anything for you.")
    assert pressure_phrases(line) == []


# ---- Round 10: invitations and reminders the person sends ------------------

def test_invite_and_reminder_drafts_pass_the_note_rules():
    from just_in_case.invites import drafts_for, invite_text, reminder_text
    from just_in_case.safeguards import check_helper_note, reading_grade
    rows = [{"id": 1, "task": "look after my pet while I'm away", "person": "Doris Chen", "date": "Nov 14", "time": "morning"},
            {"id": 2, "task": "Drive me home after my procedure", "person": "", "date": "", "time": ""},
            {"id": 3, "task": "Pick up groceries", "person": "Samantha", "date": "December 3", "time": "2 pm"}]
    for r in rows:
        for text in (invite_text(r), reminder_text(r)):
            assert check_helper_note(text) is None          # no pressure words, an easy no, grade 8 or lower
            assert reading_grade(text) <= 8
    d = drafts_for(rows)
    assert all(v["invite_ok"] and v["reminder_ok"] for v in d.values())
    assert d[1]["invite"].startswith("Hi Doris,") and "Look after my pet while I'm away, Nov 14, morning" in d[1]["invite"]
    assert d[2]["invite"].startswith("Hi,\n")                # no name: no made-up name
    assert "no need to reply" in d[1]["invite"] and "completely fine" in d[1]["reminder"]


def test_reminder_shows_only_a_few_days_before_and_only_with_someone_asked():
    from datetime import date, timedelta
    from just_in_case.invites import REMIND_DAYS_BEFORE, read_task_date, remind_now
    today = date(2026, 10, 25)
    assert REMIND_DAYS_BEFORE == 3
    day = lambda n: (today + timedelta(days=n)).isoformat()
    doris = {"person": "Doris"}
    assert remind_now({**doris, "date": day(0)}, today) and remind_now({**doris, "date": day(3)}, today)
    assert not remind_now({**doris, "date": day(4)}, today)              # too early
    assert not remind_now({**doris, "date": day(-1)}, today)             # already past
    assert not remind_now({"person": "", "date": day(1)}, today)         # no one asked yet
    assert remind_now({"person": "", "invited": True, "date": day(1)}, today)
    assert not remind_now({**doris, "date": "sometime soon"}, today)     # a date we can't read: no button
    assert read_task_date("Oct 27", today) == date(2026, 10, 27) and read_task_date("2/31", today) is None


def test_nothing_is_ever_sent_automatically_or_from_the_server():
    import pathlib
    import re as _re
    root = pathlib.Path(__file__).resolve().parent.parent
    server_code = "\n".join(p.read_text() for p in [*root.glob("just_in_case/*.py"), *root.glob("web/*.py")])
    for banned in ("smtplib", "twilio", "sendgrid", "mailgun", "aiosmtplib", "boto3", "vonage", "plivo"):
        assert banned not in server_code.lower(), banned                 # no way to send email or texts
    from just_in_case.tools import GATED_TOOLS, build_server, Toolbox
    from just_in_case.directory import Directory
    from just_in_case.state import PlanState
    _, names = build_server(Toolbox(PlanState(), Directory()), trace=lambda *_: None)
    assert not [n for n in names if _re.search(r"send|text_|email|sms|message|notify|remind", n)]
    js = (root / "web" / "static" / "app.js").read_text()
    # Text and email links are only filled in when tapped; the page never opens them by itself.
    assert not _re.search(r"location(\.href)?\s*=\s*[^;]*(sms:|mailto:)", js)
    assert not _re.search(r"window\.open\([^)]*(sms:|mailto:)", js)
    assert not _re.search(r"\b(sms|mail)\.click\(\)", js)


def test_sending_wording_is_accurate_and_t6_keeps_its_safety_check():
    import json
    import pathlib
    from just_in_case.agent import describe
    from just_in_case.config import AGENT_NAME
    from just_in_case.invites import YOU_SEND_LINE
    assert YOU_SEND_LINE == f"{AGENT_NAME} writes the message. You send it from your own phone or email."
    shown = describe("draft_helper_note", {"helper_names": ["Doris"], "role": "feed Fifi", "note_text": "Hi Doris."})
    assert YOU_SEND_LINE in shown and "NOT be sent" not in shown
    root = pathlib.Path(__file__).resolve().parent.parent
    t6 = next(c for c in json.loads((root / "eval" / "cases.json").read_text())["cases"] if c["id"] == "T6")
    assert "no_send_claim" in t6["checks"] and "note_drafted" in t6["checks"]   # never claims it sent; prepares the draft
    js = (root / "web" / "static" / "app.js").read_text()
    assert "You send it yourself" not in js and "We never send it" not in js


def test_day_before_checklist_only_lists_parts_the_user_said_yes_to():
    from datetime import date
    from just_in_case import timeline
    from just_in_case.safeguards import reading_grade
    from just_in_case.state import PlanItem, PlanState
    st = PlanState()
    st.confirmed_needs = ["ride_home_after_procedure", "spare_key_access", "pet_care_during_stay"]
    st.items = [PlanItem("ride_home_after_procedure", "service", "svc_fw_escort", "FW", user_ok=True),
                PlanItem("spare_key_access", "preparation", "prep_lockbox", "Lockbox", user_ok=True),
                PlanItem("pet_care_during_stay", "person", None, "Doris", user_ok=False)]   # not said yes yet
    today = date(2026, 10, 25)
    assert timeline.build(st, today)["day_before"] is None          # no date picked: no checklist
    st.event_date = "2026-11-14"
    db = timeline.build(st, today)["day_before"]
    assert db["date"] == "2026-11-13" and not db["past"]
    assert db["items"] == ["Confirm the ride home and the pick-up time.", "Put a key in the lockbox.",
                           "Charge the phone, and keep it close."]
    for line in timeline.DAY_BEFORE.values():
        assert reading_grade(line) <= 8


def test_why_this_is_built_from_facts_and_easy_to_read():
    from just_in_case.directory import Directory
    from just_in_case.safeguards import reading_grade
    from just_in_case.state import NEEDS, PlanItem, PlanState
    from just_in_case.why import why_this
    d, st = Directory(), PlanState()
    st.helper_notes = [{"helpers": ["Doris"], "role": "", "text": ""}]
    assert why_this(PlanItem("ride_home_after_procedure", "service", "svc_fw_escort", ""), st, d) == \
        "It's free, and it serves Boston. It helps with getting home after a procedure."
    assert "waitlist" in why_this(PlanItem("transportation", "service", "svc_mbta_ride", ""), st, d)
    assert why_this(PlanItem("pet_care_during_stay", "person", None, ""), st, d) == "You said you'd feel comfortable asking Doris."
    assert why_this(PlanItem("power_outage", "blank", None, ""), st, d).startswith("Nothing on our list fits this yet")
    for s in d.services:                       # every service, every need it covers: grade 8 or lower
        for need in s["needs"]:
            assert reading_grade(why_this(PlanItem(need, "service", s["id"], ""), st, d)) <= 8, (s["id"], need)
    for p in d.preparations:
        assert reading_grade(why_this(PlanItem(p["needs"][0], "preparation", p["id"], ""), st, d)) <= 8, p["id"]


# ---- Round 12: the agent watches over the plan ----------------------------

def test_what_needs_attention_lists_only_real_gaps():
    from datetime import date
    from just_in_case import attention
    from just_in_case.directory import Directory
    from just_in_case.state import PlanItem, PlanState
    d, today = Directory(), date(2026, 10, 25)
    st = PlanState()
    st.confirmed_needs = ["ride_home_after_procedure", "transportation", "power_outage", "pet_care_during_stay"]
    st.items = [PlanItem("ride_home_after_procedure", "service", "svc_fw_escort", "FW", user_ok=True),
                PlanItem("transportation", "service", "svc_mbta_ride", "RIDE", user_ok=True),
                PlanItem("power_outage", "blank", None, "Not covered yet")]
    st.helper_tasks = [{"id": 1, "task": "Feed Fifi", "person": "Doris Chen", "date": "", "time": ""},
                       {"id": 2, "task": "Drive me home", "person": "Leo", "confirmed": True, "date": "", "time": ""},
                       {"id": 3, "task": "Get groceries", "person": "", "date": "", "time": ""}]           # open: not a gap
    kinds = [(a["kind"], a.get("need") or a.get("task_id")) for a in attention.check(st, d, today)]
    assert ("uncovered", "power_outage") in kinds and ("uncovered", "pet_care_during_stay") in kinds
    assert ("waitlist", "transportation") in kinds and ("unconfirmed", 1) in kinds
    assert ("unconfirmed", 2) not in kinds and ("unconfirmed", 3) not in kinds
    assert not any(k == "old_info" for k, _ in kinds)                     # every service was checked recently
    later = date(2027, 6, 1)
    assert any(a["kind"] == "old_info" and a["need"] == "ride_home_after_procedure" for a in attention.check(st, d, later))
    clean = PlanState(); clean.confirmed_needs = ["ride_home_after_procedure"]
    clean.items = [PlanItem("ride_home_after_procedure", "service", "svc_fw_escort", "FW", user_ok=True)]
    assert attention.check(clean, d, today) == []
    assert attention.ALL_SET == "You're all set. Nothing needs your attention right now."


def test_my_plans_changed_moves_dates_and_drafts_kind_updates():
    from datetime import date
    from just_in_case.directory import Directory
    from just_in_case.invites import update_text
    from just_in_case.safeguards import check_helper_note, reading_grade
    from just_in_case.state import PlanItem, PlanState
    from just_in_case.tools import Toolbox, ToolError
    import pytest
    st = PlanState()
    st.confirmed_needs = ["ride_home_after_procedure"]
    st.items = [PlanItem("ride_home_after_procedure", "service", "svc_fw_escort", "FW", user_ok=True)]
    st.event_date = "2026-11-14"
    st.helper_tasks = [{"id": 1, "task": "Drive me home", "person": "Leo Park", "date": "Nov 14", "time": "3 pm"},
                       {"id": 2, "task": "Feed Fifi", "person": "", "invited": True, "date": "Nov 10", "time": ""},
                       {"id": 3, "task": "Groceries", "person": "", "date": "Nov 14", "time": ""}]
    st.helper_notes = [{"helpers": ["Doris"], "role": "", "text": ""}]
    box = Toolbox(st, Directory(), today=date(2026, 10, 25))
    with pytest.raises(ToolError):
        box.user_plans_changed("someday")
    out = box.user_plans_changed("2026-11-20", "9 am")
    assert st.event_date == "2026-11-20" and st.event_time == "9 am"
    assert [t["date"] for t in st.helper_tasks] == ["Nov 20", "Nov 10", "Nov 20"]   # only tasks on the old day move
    assert out["when"] == "Friday, November 20, at 9 am"
    assert [d["to"] for d in out["drafts"]] == ["Leo Park", "", "Doris"]           # each helper once; open tasks skipped
    assert out["drafts"][0]["text"].startswith("Hi Leo,") and "my procedure has moved" in out["drafts"][0]["text"]
    for d in out["drafts"]:
        assert check_helper_note(d["text"]) is None and reading_grade(d["text"]) <= 8
    assert check_helper_note(update_text("", "plans", "Monday, May 4")) is None


def test_night_before_summary_is_built_from_the_plan_and_shows_on_the_day_before():
    from datetime import date
    from just_in_case import timeline
    from just_in_case.directory import Directory
    from just_in_case.state import PlanItem, PlanState
    d = Directory()
    st = PlanState()
    st.confirmed_needs = ["ride_home_after_procedure", "spare_key_access", "pet_care_during_stay"]
    st.items = [PlanItem("ride_home_after_procedure", "service", "svc_fw_escort", "FW", user_ok=True),
                PlanItem("spare_key_access", "preparation", "prep_key_lockbox", "Lockbox", user_ok=True),
                PlanItem("pet_care_during_stay", "person", None, "Doris", user_ok=False)]   # not said yes: left out
    st.helper_tasks = [{"id": 1, "task": "Feed Fifi", "person": "Doris Chen", "time": "morning", "date": ""}]
    assert timeline.night_before(st, d, date(2026, 10, 25)) is None              # no date yet
    st.event_date = "2026-11-14"
    early = timeline.night_before(st, d, date(2026, 10, 25))
    assert early["show_now"] is False and early["date"] == "2026-11-13"            # a preview until then
    nb = timeline.night_before(st, d, date(2026, 11, 13))
    assert nb["show_now"] is True and nb["title"] == "Tomorrow is your procedure."
    assert nb["lines"] == ["Getting home: FriendshipWorks Medical Escort, 617-482-1510.", "Your key: in the lockbox.",
                           "Share your lockbox code only directly with someone you trust. Just in Case never stores it.",
                           "Doris: Feed Fifi, morning.", "In an emergency, call 911."]


def test_lockbox_safety_line_appears_wherever_a_lockbox_does():
    from datetime import date
    from just_in_case import timeline
    from just_in_case.card import build_card_lines, render_plan_html
    from just_in_case.directory import Directory
    from just_in_case.prompts import SYSTEM_PROMPT
    from just_in_case.safeguards import LOCKBOX_LINE
    from just_in_case.state import PlanState
    from just_in_case.summary import render_summary_html
    from just_in_case.tools import Toolbox
    assert LOCKBOX_LINE == "Share your lockbox code only directly with someone you trust. Just in Case never stores it."
    st = PlanState()
    box = Toolbox(st, Directory(), today=date(2026, 10, 25))
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    assert LOCKBOX_LINE in st.items[0].flags                       # added by code, like the escort rule
    box.user_confirm_item("spare_key_access", True)
    st.event_date = "2026-11-14"
    tl = timeline.build(st, box.today)
    assert all(s["note"] == LOCKBOX_LINE for s in tl["steps"] if s["need"] == "spare_key_access")
    assert tl["day_before"]["note"] == LOCKBOX_LINE
    assert LOCKBOX_LINE in timeline.night_before(st, box.dir, box.today)["lines"]
    card = {"name": "", "contacts": [], "medications": [], "allergies": [], "papers": "", "pet": None, "sections": ["plan"]}
    assert f"    {LOCKBOX_LINE}" in build_card_lines(card, st, box.dir, box.today)
    assert LOCKBOX_LINE in render_plan_html(st, box.dir, box.today)
    assert LOCKBOX_LINE in render_summary_html(st, box.dir, box.today)
    assert "Never ask for, repeat, or write down a lockbox code" in SYSTEM_PROMPT


def test_my_people_invite_template_passes_the_note_checks():
    """Round 13: the reusable invite in My people is fixed wording with a name slot, checked like every note."""
    from just_in_case.invites import PEOPLE_TEMPLATE, NAME_SLOT, people_invite_text
    from just_in_case.safeguards import check_helper_note
    for name in ("Leo", "Doris Chen", "Marion", ""):
        text = people_invite_text(name)
        assert check_helper_note(text) is None, text
        assert NAME_SLOT not in text
    assert people_invite_text("Doris Chen").startswith("Hi Doris,")      # first name only
    assert people_invite_text("").startswith("Hi,")
    assert PEOPLE_TEMPLATE.count(NAME_SLOT) == 1


def test_availability_fits_only_when_days_and_times_match():
    """Round 13: plain rules decide whether a helper's free times fit a task. Unknown never counts against anyone."""
    from datetime import date
    from just_in_case.availability import fits, free_words, part_of_day
    today = date(2026, 11, 1)
    mon_3pm = {"date": "2026-11-23", "time": "3 pm"}
    assert fits(mon_3pm, "weekdays", "afternoons", today)
    assert not fits(mon_3pm, "weekends", "any", today)
    assert not fits(mon_3pm, "any", "mornings", today)
    assert fits({"date": "", "time": ""}, "weekends", "mornings", today)            # nothing to compare: it fits
    assert fits({"date": "Tuesdays", "time": "3:00"}, "weekends", "mornings", today)  # can't read these for sure
    assert [part_of_day(t) for t in ("9:30 AM", "3 pm", "12 pm", "12 am", "6:15 PM", "15:00", "evening", "Morning")] == \
        ["mornings", "afternoons", "afternoons", "mornings", "evenings", "afternoons", "evenings", "mornings"]
    assert free_words("weekdays", "afternoons") == "weekday afternoons" and free_words("any", "any") == "any time"
    assert free_words("any", "mornings") == "mornings" and free_words("weekends", "any") == "weekends"


def test_thank_you_and_reminder_notes_pass_the_note_checks():
    from just_in_case.invites import PEOPLE_REMINDER, thank_you_text
    from just_in_case.safeguards import check_helper_note
    assert check_helper_note(thank_you_text("Doris Chen", "Pick up groceries")) is None
    assert thank_you_text("Doris Chen", "Pick up groceries").startswith("Hi Doris,")
    reminder = PEOPLE_REMINDER.replace("{name}", "Leo").replace("{task}", "Drive me home after my procedure")
    assert check_helper_note(reminder) is None


def test_whats_new_is_built_by_code_from_the_offers():
    """Round 13: who can help, times that don't fit, two offers to choose from, and no offers close to the date."""
    from datetime import date
    from just_in_case.directory import Directory
    from just_in_case.replies import whats_new
    from just_in_case.state import PlanState
    today = date(2026, 11, 20)
    st = PlanState(town="Boston")
    st.helper_tasks = [
        {"id": 1, "task": "Drive me home", "date": "2026-11-23", "time": "3 pm", "person": "Leo", "confirmed": False,
         "offers": [{"id": 1, "name": "Leo", "days": "weekdays", "times": "afternoons", "note": ""}]},
        {"id": 2, "task": "Pick up groceries", "date": "2026-11-23", "time": "3 pm", "person": "", "confirmed": False,
         "offers": [{"id": 1, "name": "Doris", "days": "any", "times": "mornings", "note": "Mornings only"}]},
        {"id": 3, "task": "Feed Fifi", "date": "", "time": "", "person": "Leo", "confirmed": False,
         "offers": [{"id": 1, "name": "Leo", "days": "any", "times": "any", "note": ""},
                    {"id": 2, "name": "Marion", "days": "weekends", "times": "any", "note": ""}]},
        {"id": 4, "task": "Drive me to my eye appointment", "date": "2026-11-22", "time": "", "person": "", "confirmed": False},
        {"id": 5, "task": "Water the plants", "date": "", "time": "", "person": "Sam", "confirmed": True,
         "offers": [{"id": 1, "name": "Sam", "days": "any", "times": "any", "note": ""}]},
    ]
    news = {n["task_id"]: n for n in whats_new(st, Directory(), today, shared=True)}
    assert news[1]["kind"] == "helping" and news[1]["text"].endswith("Weekday afternoons work for Leo.")
    assert news[2]["kind"] == "mismatch" and "Doris is free mornings, but this task is at 3 pm." in news[2]["text"]
    assert news[2]["text"].endswith("Would you like to ask someone else, or change the time?")
    assert news[3]["kind"] == "choose" and [c["name"] for c in news[3]["choices"]] == ["Leo", "Marion"]
    assert news[4]["kind"] == "soon" and "in 2 days" in news[4]["text"] and news[4]["backup"]["name"]   # a checked service
    assert 5 not in news                                                       # confirmed: nothing new
    assert whats_new(st, Directory(), today, shared=False)[-1]["task_id"] != 6



def test_follow_up_text_passes_the_note_checks():
    from just_in_case.invites import follow_up_text
    from just_in_case.safeguards import check_helper_note
    text = follow_up_text({"task": "drive me home", "date": "Nov 24", "time": "3 pm"}, "David Lee")
    assert text.startswith("Hi David,") and "Drive me home, Nov 24, 3 pm" in text and check_helper_note(text) is None
