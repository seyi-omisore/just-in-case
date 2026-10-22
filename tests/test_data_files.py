"""Checks the REAL data files in data/ for typos and missing fields.

Run after editing services.json or preparations.json:  pytest -q
Each failure message says which entry and which field to fix.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from just_in_case.state import NEEDS

DATA = Path(__file__).resolve().parent.parent / "data"
SERVICE_FIELDS = {"id", "name", "needs", "areas", "cost", "waitlist", "waitlist_note",
                  "last_verified", "phone", "url", "how_to_use", "sample"}
PREP_FIELDS = {"id", "name", "needs", "what", "rough_cost"}


def load(name):
    return json.loads((DATA / name).read_text())


SERVICES = load("services.json")["services"]
PREPS = load("preparations.json")["preparations"]


@pytest.mark.parametrize("svc", SERVICES, ids=lambda s: s.get("id", "?"))
def test_service_entry(svc):
    missing = SERVICE_FIELDS - svc.keys()
    assert not missing, f"{svc.get('id')}: missing fields {missing}"
    assert svc["id"].startswith("svc_"), f"{svc['id']}: id must start with svc_"
    bad = [n for n in svc["needs"] if n not in NEEDS]
    assert not bad, f"{svc['id']}: unknown needs {bad}. Allowed: {list(NEEDS)}"
    assert svc["cost"] in ("free", "low", "paid"), f"{svc['id']}: cost must be free, low, or paid"
    assert isinstance(svc["waitlist"], bool), f"{svc['id']}: waitlist must be true or false"
    assert isinstance(svc["sample"], bool), f"{svc['id']}: sample must be true or false"
    try:
        date.fromisoformat(svc["last_verified"])
    except ValueError:
        pytest.fail(f"{svc['id']}: last_verified must look like 2026-10-20")
    assert svc["areas"], f"{svc['id']}: areas cannot be empty (use [\"*\"] for statewide)"


@pytest.mark.parametrize("prep", PREPS, ids=lambda p: p.get("id", "?"))
def test_preparation_entry(prep):
    missing = PREP_FIELDS - prep.keys()
    assert not missing, f"{prep.get('id')}: missing fields {missing}"
    assert prep["id"].startswith("prep_"), f"{prep['id']}: id must start with prep_"
    bad = [n for n in prep["needs"] if n not in NEEDS]
    assert not bad, f"{prep['id']}: unknown needs {bad}"


def test_ids_are_unique():
    ids = [s["id"] for s in SERVICES] + [p["id"] for p in PREPS]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate ids: {dupes}"
