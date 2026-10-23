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
    assert csv_text.splitlines()[0] == "Task,Date,Time,Who is helping"
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
