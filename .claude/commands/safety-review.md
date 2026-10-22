---
description: Review uncommitted changes against the Just in Case safety rules and basic Python security. Adapted from the Everything Claude Code security-reviewer for this project.
---

# Safety Review

Review the changes from `git diff HEAD` (or the files named in $ARGUMENTS). Read each
changed file in full. Report findings as CRITICAL, HIGH, MEDIUM, or LOW with file and line.

## 1. Just in Case rules (any break is CRITICAL)

- The agent gains no new way to send messages, browse, run commands, or read or write files
  outside `output/`. In `agent.py`, `tools=[]` and `setting_sources=[]` must stay, and the
  permission gate must still deny tools that do not start with `mcp__jic__`.
- `confirm_needs`, `draft_helper_note`, and `make_fridge_card` are still in `GATED_TOOLS`
  and still left out of `allowed_tools`.
- `add_to_plan` still rejects service ids that are not in the directory.
- The "person" route and helper notes are still blocked unless `people_allowed()` is true.
- The card is still built by code from user-entered fields, with the 911 line.
- No real-looking phone numbers, names, or services are added outside `data/`.
- Every new rule has a test in `tests/test_safeguards.py`.

## 2. Python security

- No API keys, tokens, or passwords in code or committed files. Keys come from `.env` only.
- No `eval`, `exec`, `pickle.load`, or `subprocess` with `shell=True`.
- User text inserted into the HTML card is escaped (`html.escape`).
- File paths built from user input are slugged, so they cannot escape `output/`.
- New dependencies in `requirements.txt` are well known and pinned to a minimum version.

## 3. Quality (MEDIUM or LOW)

- Plain user-facing language, grade 8 or lower, no pressure words.
- Functions under about 50 lines; clear names; no leftover debug prints.

## Output

List the findings, then a decision: SAFE TO COMMIT, or FIX FIRST (with the reasons).
Run `pytest -q` at the end and include the result.
