"""Loads the curated service and preparation lists and ranks matches.

Ranking is done here in plain Python, not by the model, so "free first" and
"flag waitlists early" are guaranteed and testable.
"""

import json
from datetime import date
from pathlib import Path

from .config import DATA_DIR, STALE_AFTER_DAYS

COST_ORDER = {"free": 0, "low": 1, "paid": 2}


class Directory:
    def __init__(self, data_dir: Path = DATA_DIR, today: date | None = None):
        self.today = today or date.today()
        self.services = json.loads((data_dir / "services.json").read_text())["services"]
        self.preparations = json.loads((data_dir / "preparations.json").read_text())[
            "preparations"
        ]
        self.by_id = {s["id"]: s for s in self.services}
        self.by_id.update({p["id"]: p for p in self.preparations})

    # ---- services -------------------------------------------------------

    def _serves_area(self, svc: dict, town: str) -> bool:
        areas = [a.lower() for a in svc["areas"]]
        return "*" in areas or town.lower() in areas

    def _days_old(self, svc: dict) -> int:
        return (self.today - date.fromisoformat(svc["last_verified"])).days

    def service_flags(self, svc: dict) -> list[str]:
        flags = []
        if svc["waitlist"]:
            flags.append(f"WAITLIST: {svc['waitlist_note'] or 'May have a wait.'} Call early.")
        if self._days_old(svc) > STALE_AFTER_DAYS:
            flags.append(
                f"NOT RECENTLY CHECKED: last verified {svc['last_verified']}. "
                "Call to confirm it still runs."
            )
        if svc.get("sample"):
            flags.append("SAMPLE ENTRY: placeholder for testing, not a real program.")
        return flags

    def find_services(self, need: str, town: str) -> list[dict]:
        matches = [
            s for s in self.services if need in s["needs"] and self._serves_area(s, town)
        ]
        # Free first, then low cost, then paid. Within a tier, no waitlist first,
        # then most recently verified.
        matches.sort(
            key=lambda s: (COST_ORDER[s["cost"]], s["waitlist"], self._days_old(s))
        )
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "cost": s["cost"],
                "how_to_use": s["how_to_use"],
                "phone": s["phone"],
                "flags": self.service_flags(s),
            }
            for s in matches
        ]

    # ---- preparations ---------------------------------------------------

    def find_preparations(self, need: str) -> list[dict]:
        return [
            {"id": p["id"], "name": p["name"], "what": p["what"], "rough_cost": p["rough_cost"]}
            for p in self.preparations
            if need in p["needs"]
        ]

    def is_service(self, ref_id: str) -> bool:
        return any(s["id"] == ref_id for s in self.services)

    def is_preparation(self, ref_id: str) -> bool:
        return any(p["id"] == ref_id for p in self.preparations)
