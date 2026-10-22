---
description: Check the Just in Case repo is healthy before a commit or a demo. Python version of the Everything Claude Code /verify command.
---

# Verify

Run these in order and report a short summary. Do not fix anything unless asked.

1. **Tests:** run `pytest -q`. Report the pass/fail count. If anything fails, show the
   failing test name and its message, then STOP.
2. **Setup:** run `python run.py --check`. Report the endpoint, model, and tool list.
3. **Secrets:** search every tracked file for `sk-or-` and for long random-looking tokens.
   `.env.example` and `.claude/settings.local.json.example` hold placeholders only, so they are fine.
   Confirm `.env` and `.claude/settings.local.json` are listed in `.gitignore`.
4. **Sample data:** count entries in `data/services.json` with `"sample": true` and list their ids.
5. **Git:** show `git status --short` and the last 3 commits.

## Output

```
VERIFY: PASS / FAIL
Tests:    X/Y passed
Setup:    OK / problem
Secrets:  OK / FOUND in <file>
Sample services remaining: N (<ids>)
Uncommitted files: N
Ready to commit: YES / NO
```
