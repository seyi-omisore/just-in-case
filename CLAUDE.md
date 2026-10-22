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
- Keep the user-facing language plain (grade 8 or lower), warm, and pressure-free.
- Run `pytest -q` before every commit. Commit small, with clear messages.
