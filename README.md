# Just in Case

**A quiet backup plan for people living alone.**

Just in Case is an agentic AI that helps older adults who live alone prepare for
common situations (getting home after a procedure, a fall, a winter power outage,
a hospital stay with a pet at home) *before* they happen. It leans on services and
simple preparation first, and on people only when the user chooses. It ends with a
printed one-page "If something happens" card for the refrigerator.

> Team: Seyi (technical), Leya (services and safeguards), Joan (evaluation and documentation), Satoshi (story and presentation)

## How it works

For every need, the agent follows a fixed decision ladder:

1. **Service** from a verified local directory (free options first, waitlists flagged)
2. **Preparation** that removes the need (a key lockbox, an emergency kit)
3. **A person**, only if the user says she is comfortable asking, with one small role
4. **Blank**, said plainly, with a pointer to the Council on Aging or Mass211

Three human checkpoints pause the agent for approval: the list of needs, any note
to a friend, and the card. The agent never sends anything.

See [docs/workflow.md](docs/workflow.md) for the diagram and [docs/TEAM_GUIDE.md](docs/TEAM_GUIDE.md) for running and testing.

## Safeguards enforced in code (not just in the prompt)

| Rule | Where |
|---|---|
| Only services from `data/services.json` can enter a plan | `tools.py` `add_to_plan` |
| Free options ranked first; waitlists and stale entries flagged | `directory.py` |
| No "person" route unless the user opted in; burden mode is sticky | `tools.py`, `state.py` |
| Notes: no pressure words, must make "no" easy, grade 8 or lower | `safeguards.py` |
| At most 3 helpers per plan | `tools.py` |
| Ride-home items always get the "check your clinic's escort rule" line | `tools.py` |
| Card built by code from user-typed details; 911 line top and bottom | `card.py` |
| No file, shell, web, or messaging tools available to the model | `agent.py` (`tools=[]`, permission gate) |

Each rule has an offline test in `tests/test_safeguards.py`.

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then paste your OpenRouter key into .env
python run.py --check            # free: checks setup
pytest -q                        # free: tests every safeguard
python run.py                    # talk to the agent
python eval/run_eval.py          # scripted test cases, writes eval/results.md
```

Cards are saved in `output/` as printable HTML.

## Built with

- [Claude Agent SDK (Python)](https://platform.claude.com/docs/en/agent-sdk/overview): agent loop, custom tools, permission callback
- [OpenRouter](https://openrouter.ai) Anthropic-compatible endpoint, Claude Haiku 5.5 model
- Plain JSON data files curated by the team

## Known limitations

- Service data in this repo is **sample data** until replaced with verified entries.
- One area (Boston) only.
- Check-ins over time are shown as a review date on the card; scheduled reminders are future work.
- It is a planning tool. It does not detect falls or monitor anyone.

## AI use disclosure

Code was written with help from Claude (Anthropic). All design decisions, data
curation, and testing were done by the team.
