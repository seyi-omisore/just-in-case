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


ALL_SECTIONS = ["contacts", "medications", "pet", "helpers", "plan"]


@pytest.fixture
def fresh(tmp_path):
    """A brand-new visit: nothing chosen yet."""
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
    txt = open(out["file"].replace(".html", ".txt")).read()
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
    txt = open(out["file"].replace(".html", ".txt")).read()
    assert CRISIS_LINE in txt


def test_card_shows_service_phone(box):
    box.confirm_needs(["ride_home_after_procedure"])
    box.add_to_plan("ride_home_after_procedure", "service", "svc_escort_volunteer")
    box.state.user_said.append("I'm Ruth.")
    out = box.make_fridge_card("Ruth", [], [])
    txt = open(out["file"].replace(".html", ".txt")).read()
    assert "SAMPLE Volunteer Medical Escort Program, TO BE VERIFIED" in txt



# ---- No assumptions on the card --------------------------------------------

def card_text(out):
    return open(out["file"].replace(".html", ".txt")).read()


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
    with pytest.raises(ToolError, match="People are not part"):
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
    txt = card_text(fresh.make_fridge_card(
        "Ruth", [{"name": "Marion", "relationship": "", "phone": "617-555-0142"}], []))
    assert "Marion" in txt and "MEDICATIONS" not in txt


def test_ticking_nothing_gives_just_the_emergency_lines(fresh):
    fresh.state.user_said.append("I'm Ruth.")
    fresh.confirm_needs(["spare_key_access"])
    fresh.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    fresh.user_edit_task("add", {"task": "Rides"})
    fresh.choose_card_sections([])
    txt = card_text(fresh.make_fridge_card("Ruth", [], []))
    assert "911" in txt and "988" in txt
    assert "MY PLAN" not in txt and "WHO IS HELPING" not in txt


def test_unknown_section_rejected(fresh):
    with pytest.raises(ToolError):
        fresh.choose_card_sections(["social_security_number"])
