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


@pytest.fixture
def box(tmp_path):
    return Toolbox(PlanState(town="Boston"), Directory(FIXTURES, today=TODAY), out_dir=tmp_path, today=TODAY)


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
    box.confirm_needs(["spare_key_access"])
    box.add_to_plan("spare_key_access", "preparation", "prep_key_lockbox")
    out = box.make_fridge_card("Ruth", [{"name": "Marion", "relationship": "friend",
                                         "phone": "617-555-0142"}], ["Eye drops, at night"])
    txt = open(out["file"].replace(".html", ".txt")).read()
    assert txt.count(EMERGENCY_LINE.upper()) + txt.count(EMERGENCY_LINE) >= 2
    assert "Eye drops, at night" in txt
    assert "Key lockbox" in txt
