# Setup Guide (for Seyi)

This explains every piece of the starter repo and how to get it running in VS Code.

---

## 1. The big picture: two different "Claudes"

There are two separate things in this project, and keeping them apart avoids most confusion.

| | **Claude Code** (your coding assistant) | **Claude Agent SDK** (the engine inside Just in Case) |
|---|---|---|
| What it is | An AI pair programmer in VS Code / terminal | A Python library you import in your code |
| Who uses it | You, while building | Ruth, when she talks to Just in Case |
| Everything Claude Code (ECC) | **Yes**, ECC plugs in here | **No**, ECC never touches the product |
| Config | `CLAUDE.md`, `.claude/settings.local.json` | `just_in_case/agent.py` |

Under the hood, the Agent SDK actually starts a small copy of Claude Code and drives it.
That is why `agent.py` sets `setting_sources=[]`: it stops the product from reading
your personal Claude Code settings, `CLAUDE.md`, or ECC plugins. Ruth's agent stays clean.

## 2. Why the OpenRouter URL is different

You were given `https://openrouter.ai/api/v1`. That is OpenRouter's **OpenAI-style** endpoint.
The Agent SDK speaks **Anthropic's** format, and OpenRouter has a matching endpoint at:

```
https://openrouter.ai/api        <- use this one (no /v1)
```

Three environment variables connect them (set for you in `config.py`):

- `ANTHROPIC_BASE_URL=https://openrouter.ai/api` (send requests to OpenRouter)
- `ANTHROPIC_AUTH_TOKEN=<your OpenRouter key>` (sent as a Bearer token)
- `ANTHROPIC_API_KEY=""` (must be empty, or Claude Code tries to log into Anthropic directly)

`config.py` refuses to start if the URL ends in `/v1`, so this mistake cannot slip in.

## 3. Get it running (about 15 minutes)

**Install once:** Python 3.10 or newer, Git, VS Code.

```bash
# 1. Put the starter files in a folder, open it in VS Code (File > Open Folder)

# 2. Make a virtual environment (a private box of packages for this project)
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
.venv\Scripts\activate             # Windows PowerShell

# 3. Install packages
pip install -r requirements.txt

# 4. Add your key
cp .env.example .env               # Windows: copy .env.example .env
#    open .env and paste your key after OPENROUTER_API_KEY=

# 5. Free checks (no money spent)
python run.py --check
pytest -q                          # should say 28 passed

# 6. Talk to the agent
python run.py
```

When VS Code asks "select interpreter", pick the one inside `.venv`.

The dim lines while chatting (`[tool]`, `[agent]`, `[rule]`) show the agent's decisions live.
Keep them on in the demo: they are how judges *see* that it is agentic. `--quiet` hides them.

## 4. File tour

```
just-in-case/
  serve.py                  start here: the website (add --demo for no AI)
  run.py                    terminal chat
  web/server.py             web server: runs the agent, keeps the key hidden
  web/demo_agent.py         scripted Ruth story for demos and testing
  web/static/               the page: index.html, style.css, app.js
  just_in_case/
    config.py               settings + OpenRouter connection
    state.py                the plan in memory; list of allowed needs
    directory.py            loads JSON data, ranks services (free first)
    safeguards.py           pressure words, readability, fixed safety lines
    tools.py                the 8 tools and all the rules
    card.py                 builds the printable fridge card
    prompts.py              the agent's instructions + greeting
    agent.py                SDK setup, approval gate, conversation loop
  data/
    services.json           real Boston services (Leya re-confirms)
    support_lines.json      988, Call2Talk, Mass 211, wellness check
    preparations.json       lockbox, emergency kit, fall-safety check...
    personas/ruth.json      demo persona + answer key
  tests/test_safeguards.py  14 safeguard tests + data-file checks
  eval/cases.json           T1, T2, T3, T6, T7, T8 as scripts
  eval/run_eval.py          runs them live, writes eval/results.md
  docs/workflow.md          diagram (renders on GitHub)
  CLAUDE.md                 notes for Claude Code while you build
```

## 5. How one turn works (the key concepts)

```
Ruth types  ->  client.query()  ->  model decides to call a tool
     -> approval_gate():  not our tool? DENY.  normal tool? ALLOW.  checkpoint? ASK RUTH.
     -> tool runs in Toolbox, which checks the rules (may reply "RULE: ...")
     -> result goes back to the model -> more tools or a text reply -> printed
```

Concepts to know (each is one line in `agent.py`):

- **`@tool` + `create_sdk_mcp_server`**: turns a Python function into a tool the model can call.
  "MCP" is just the standard plug format for tools. "In-process" means no separate server to run.
- **`tools=[]`**: turns off Claude Code's built-in tools (shell, files, web). The model can only use our 8.
- **`allowed_tools`**: tools that run without asking. The 3 checkpoint tools are left out on purpose.
- **`can_use_tool`**: our function, called for every tool not in `allowed_tools`. This is the
  human-in-the-loop gate. It also refuses any tool that is not ours.
- **`system_prompt`**: replaces Claude Code's default coding instructions with ours.
- **`max_budget_usd`**: a hard spending stop per session.

**Why rules live in code, not just the prompt.** A prompt is a request. Code is a guarantee.
If the model tries to add a service that is not in the directory, `add_to_plan` refuses and tells
it why. If it writes a pushy note, the gate sends it back before Ruth ever sees it. This is the
strongest thing you can say in the Responsible AI section: "we can prove it, here are the tests."

## 6. Budget ($50)

- Claude Haiku 5.5 on OpenRouter is about $0.10 per million input tokens and $0.50 per million
  output tokens. A full demo conversation costs well under a cent. Running the agent will not
  dent your $50.
- **What can dent it is Claude Code itself** (the coding assistant) if you point it at the same
  key and use a big model for hours. Use the Sonnet alias for real coding work, watch the
  OpenRouter Activity page, and consider setting a separate limit. If you have a Claude
  subscription, logging Claude Code in with that instead keeps the $50 for the product.
- For the recorded demo, try `JIC_MODEL=~anthropic/claude-sonnet-latest` in `.env` and compare
  tone. Check its price on openrouter.ai first.

## 7. Claude Code in VS Code + Everything Claude Code

1. Install the **Claude Code** extension in VS Code (VS Code will suggest it from `.vscode/extensions.json`).
2. Copy `.claude/settings.local.json.example` to `.claude/settings.local.json` and paste your key.
   (It is gitignored, so the key never reaches GitHub.) If you were ever logged into Claude Code
   with an Anthropic account, run `/logout` once, then restart.
3. In Claude Code, run `/status`. It should show `ANTHROPIC_AUTH_TOKEN` and `https://openrouter.ai/api`.
4. **Everything Claude Code is already included, only the parts we need.** The full ECC repo is
   thousands of files, mostly for JavaScript projects, so we do NOT install it. Instead the
   project has four small command files in `.claude/commands/` (about 25 KB total):

   | Command | Source | Use it |
   |---|---|---|
   | `/plan` | ECC original, unchanged | before building a new feature |
   | `/code-review` | ECC original, unchanged | before each commit |
   | `/verify` | ECC idea, rewritten for Python | before a commit or a demo |
   | `/safety-review` | ECC security-reviewer, rewritten for Just in Case | after any change to tools or agent code |

   Claude Code picks these up automatically when you open the project folder. Type `/` in
   Claude Code to see them. Because they live in the repo, teammates get them too.
   ECC is MIT licensed; its license is kept in `.claude/commands/ECC-LICENSE`.

## 8. GitHub

```bash
git init
git add .
git commit -m "Starter: agent, tools, safeguards, tests, eval harness"
# create an empty repo on github.com named just-in-case, then:
git remote add origin https://github.com/seyi-omisore/just-in-case.git
git branch -M main
git push -u origin main
```

If the repo is private, add **TinyZhen** as a collaborator (Settings > Collaborators).
Commit small and often: the brief asks for version history.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| "No OpenRouter key found" | `.env` missing or key not pasted |
| Auth error / 401 | Key wrong or expired; `ANTHROPIC_API_KEY` must be empty |
| Model not found | Check the exact model id on openrouter.ai/models and update `JIC_MODEL` |
| Run stops with `error_max_budget_usd` | Raise `JIC_MAX_BUDGET_USD` in `.env` |
| Agent loops on a "RULE:" reply | Read the rule message; tighten the prompt or the tool description |
