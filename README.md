# Just in Case

**A quiet backup plan for people living alone.**

Just in Case is an agentic AI that helps older adults who live alone prepare for
common situations (getting home after a procedure, a fall, a winter power outage,
a hospital stay with a pet at home) *before* they happen. It leans on services and
simple preparation first, and on people only when the user chooses. It ends with a
printed one-page "If something happens" card for the refrigerator.

> Team: Leya Jacob Kurzhiparambil (services and safeguards), Oluwaseyi Omisore (technical), Joan Arches (evaluation and documentation), Satoshi Nagae (story, design, and presentation)

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
| Card uses only details the user typed, and only sections the user ticked | `tools.py` `make_fridge_card`, `safeguards.py` `said_by_user` |
| Notes and table rows only name people the user named | `tools.py` |
| Nothing is saved unless the user agrees; the agent has no sharing tool | `web/server.py`, consent box on the page |
| Shared lists: random links, separate owner key, hashed sign-up keys, 30-day deletion | `web/share_store.py` |
| Spreadsheet download cannot run formulas (CSV injection guard) | `safeguards.py` `safe_cell` |

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
python serve.py                  # the website, with the live agent
python serve.py --demo           # the website, scripted demo (no AI, no cost)
python run.py                    # talk to the agent in the terminal
python eval/run_eval.py          # scripted test cases, writes eval/results.md
```

Cards are saved in `output/` as printable HTML.

## Remake the tour video

The About tab shows a 1-minute tour of Ruth's made-up example. After the app changes, remake it:

```bash
pip install -r requirements-video.txt    # once: Playwright and an ffmpeg program, inside .venv
python -m playwright install ffmpeg      # once: the small helper Playwright records with
python scripts/make_tour_video.py        # about a minute; add --no-voice to leave out narration
```

It starts the app in demo mode (Light theme, phone size), plays Ruth's guided story, keeps the main
moments, and saves `tour.mp4`, `tour.vtt` (captions), and `tour-poster.jpg` in `web/static/img/`.
It records with your Google Chrome, or Playwright's Chromium if Chrome is missing. On a Mac, the
built-in voice reads each caption quietly. Only Ruth's made-up details appear in the video.

## Built with

- [Claude Agent SDK (Python)](https://platform.claude.com/docs/en/agent-sdk/overview): agent loop, custom tools, permission callback
- [OpenRouter](https://openrouter.ai) Anthropic-compatible endpoint, Claude Haiku 5.5 model
- Plain JSON data files curated by the team

## Known limitations

- Services were copied from official websites on 2026-10-21 and are being re-confirmed. Always call to confirm.
- One area (Boston) only.
- Check-ins over time are shown as a review date on the card; scheduled reminders are future work.
- It is a planning tool. It does not detect falls or monitor anyone.
- Shared helper lists are saved as small files on the server (with the user's consent) and deleted after 30 days.

## AI use disclosure

Code was written with help from Claude (Anthropic). All design decisions, data
curation, and testing were done by the team.
