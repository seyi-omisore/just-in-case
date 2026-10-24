# Project notes for Claude Code (the coding assistant)

This file is read by Claude Code while we BUILD the project in VS Code. It is not
read by the Just in Case agent at runtime (agent.py sets setting_sources=[]).

## What this is
Just in Case: a Python agent (Claude Agent SDK, routed through OpenRouter) that
helps older adults who live alone make a small backup plan and a fridge card.

## Layout
- `just_in_case/tools.py`: all tool logic (Toolbox) + SDK wrappers. Rules live here.
- `just_in_case/safeguards.py`: pressure words, readability, fixed safety lines.
- `just_in_case/agent.py`: SDK options, approval gate, conversation loop.
- `just_in_case/prompts.py`: system prompt (teammates edit wording here).
- `data/*.json`: curated services and preparations (sample until verified).
- `tests/`: offline tests, no API cost. `eval/`: scripted live test cases.

## Rules when changing code
- Safety rules belong in code (tools.py / safeguards.py), not only in the prompt.
  Every new rule gets a test in tests/test_safeguards.py.
- Never give the agent a tool that sends messages, browses, or touches files
  other than writing the card into output/.
- Never add real-looking phone numbers or services. Only Leya adds verified data.
- Keep the user-facing language plain (about grade 6), warm, and pressure-free.
- Each card shows at most two short sentences. Longer text goes behind a
  `<details class="more">` "Show details" link. `tests/test_web.py` checks this.
- The agent's name is `AGENT_NAME` in `just_in_case/config.py`. Never type it anywhere else. Pages use `{{AGENT_NAME}}`.
- Keep the agent's private log folder (`CLAUDE_CONFIG_DIR` in agent.py). Without it the AI toolkit
  saves a copy of every chat in `~/.claude`. If a privacy claim changes, update the Your privacy page too.
- Run `pytest -q` before every commit. Commit small, with clear messages.
