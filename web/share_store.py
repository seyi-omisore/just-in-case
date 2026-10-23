"""Shared helper lists. The ONLY thing Just in Case saves, and only with consent.

What is saved (one small JSON file per list, in output/shared/):
  - the name the user chose to show (optional)
  - the helper table rows: task, date, time, person
  - when it was made and when it is deleted (30 days, or sooner if she deletes it)
Nothing else: no chat, no card, no contacts, no medications.

Security:
  - Links use long random tokens (secrets.token_urlsafe), so they cannot be guessed.
  - The owner gets a separate secret token to change or delete the list.
  - A helper who signs up gets a token to remove their own name. Only a hash of
    that token is stored, so a leaked file cannot be used to remove names.
  - Every field has a length limit. The page shows everything as plain text.
"""

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path

KEEP_DAYS = 30
MAX_FIELD = 120
MAX_ROWS = 40


class ShareError(Exception):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _clean(v) -> str:
    return str(v or "").replace("\r", " ").replace("\n", " ").strip()[:MAX_FIELD]


def first_name(v) -> str:
    """The shared page shows first names only: "Doris Chen" -> "Doris"."""
    parts = _clean(v).split()
    return parts[0].rstrip(",.") if parts else ""


class ShareStore:
    def __init__(self, folder: Path, keep_days: int = KEEP_DAYS):
        self.folder = Path(folder)
        self.keep_seconds = keep_days * 86400
        self.lock = threading.Lock()

    # ---- files ---------------------------------------------------------------
    def _path(self, share_id: str) -> Path:
        if not share_id or not all(c.isalnum() or c in "-_" for c in share_id):
            raise ShareError("Not found")
        return self.folder / f"{share_id}.json"

    def _load(self, share_id: str) -> dict:
        p = self._path(share_id)
        if not p.exists():
            raise ShareError("Not found")
        rec = json.loads(p.read_text())
        if rec["expires"] < time.time():
            p.unlink(missing_ok=True)
            raise ShareError("Not found")
        return rec

    def _save(self, rec: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self._path(rec["id"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        tmp.replace(p)  # atomic: a reader never sees half a file

    # ---- owner actions -------------------------------------------------------
    def create(self, label: str, tasks: list[dict]) -> tuple[dict, str]:
        """Make a shared list. Returns (record, owner_token)."""
        owner_token = secrets.token_urlsafe(24)
        now = time.time()
        rec = {
            "id": secrets.token_urlsafe(16),
            "owner_hash": _hash(owner_token),
            "label": _clean(label),
            "created": now,
            "expires": now + self.keep_seconds,
            "tasks": [],
        }
        rec["tasks"] = self._merge([], tasks)
        with self.lock:
            self._save(rec)
        return rec, owner_token

    def is_owner(self, rec: dict, owner_token: str | None) -> bool:
        return bool(owner_token) and secrets.compare_digest(rec["owner_hash"], _hash(owner_token))

    def _merge(self, old: list[dict], tasks: list[dict]) -> list[dict]:
        """Owner's table is the source of truth for rows; keep helpers' sign-ups."""
        claims = {t["id"]: t.get("claim_hash") for t in old}
        out = []
        for t in tasks[:MAX_ROWS]:
            row = {k: _clean(t.get(k)) for k in ("task", "date", "time", "person")}
            row["id"] = int(t["id"])
            row["claim_hash"] = claims.get(row["id"]) if row["person"] else None
            out.append(row)
        return out

    def update_tasks(self, share_id: str, tasks: list[dict]) -> dict:
        with self.lock:
            rec = self._load(share_id)
            rec["tasks"] = self._merge(rec["tasks"], tasks)
            self._save(rec)
            return rec

    def delete(self, share_id: str, owner_token: str) -> None:
        with self.lock:
            rec = self._load(share_id)
            if not self.is_owner(rec, owner_token):
                raise ShareError("Only the person who made this list can delete it.")
            self._path(share_id).unlink(missing_ok=True)

    # ---- helper actions ------------------------------------------------------
    def claim(self, share_id: str, task_id: int, name: str) -> tuple[dict, str]:
        name = _clean(name)
        if not name:
            raise ShareError("Please type your name.")
        with self.lock:
            rec = self._load(share_id)
            row = next((t for t in rec["tasks"] if t["id"] == task_id), None)
            if row is None:
                raise ShareError("That task is no longer on the list.")
            if row["person"]:
                raise ShareError(f"{row['person']} is already helping with this one.")
            token = secrets.token_urlsafe(16)
            row["person"], row["claim_hash"] = name, _hash(token)
            self._save(rec)
            return rec, token

    def unclaim(self, share_id: str, task_id: int, token: str) -> dict:
        with self.lock:
            rec = self._load(share_id)
            row = next((t for t in rec["tasks"] if t["id"] == task_id), None)
            if row is None:
                raise ShareError("That task is no longer on the list.")
            mine = row.get("claim_hash") and secrets.compare_digest(row["claim_hash"], _hash(token or ""))
            if not (mine or self.is_owner(rec, token)):
                raise ShareError("You can only remove your own name.")
            row["person"], row["claim_hash"] = "", None
            self._save(rec)
            return rec

    # ---- reading ---------------------------------------------------------------
    def get(self, share_id: str) -> dict:
        return self._load(share_id)

    @staticmethod
    def public(rec: dict) -> dict:
        """What the share page may see. No tokens, no hashes, and first names only."""
        return {
            "label": first_name(rec["label"]),
            "expires": rec["expires"],
            "tasks": [{**{k: t[k] for k in ("id", "task", "date", "time")}, "person": first_name(t["person"])}
                      for t in rec["tasks"]],
        }

    def purge_expired(self) -> int:
        n = 0
        if not self.folder.exists():
            return 0
        for p in self.folder.glob("*.json"):
            try:
                if json.loads(p.read_text())["expires"] < time.time():
                    p.unlink(missing_ok=True)
                    n += 1
            except Exception:
                continue
        return n
