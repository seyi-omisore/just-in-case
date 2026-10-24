"""Run Just in Case in the terminal.

    python run.py            # talk to the agent
    python run.py --check    # check setup without calling the model (free)
    python run.py --quiet    # hide the [agent]/[tool]/[rule] trace lines
"""

import argparse
import asyncio

from just_in_case.config import MODEL, MAX_BUDGET_USD, runtime_env
from just_in_case.directory import Directory
from just_in_case.config import AGENT_NAME
from just_in_case.prompts import GREETING
from just_in_case.state import PlanState
from just_in_case.tools import Toolbox, build_server


def check() -> None:
    env = runtime_env()  # exits with a clear message if the key is missing
    d = Directory()
    _, names = build_server(Toolbox(PlanState(), d), trace=lambda *_: None)
    print("Setup looks good.")
    print(f"  Endpoint : {env['ANTHROPIC_BASE_URL']}")
    print(f"  Model    : {MODEL}")
    print(f"  Budget   : ${MAX_BUDGET_USD:.2f} per session")
    print(f"  Services : {len(d.services)}   Preparations: {len(d.preparations)}")
    print("  Tools    : " + ", ".join(n.split('__')[-1] for n in names))


async def chat(quiet: bool) -> None:
    from just_in_case.agent import JustInCaseAgent

    trace = (lambda *_: None) if quiet else (lambda s: print(f"\033[2m{s}\033[0m") if s else None)
    async with JustInCaseAgent(trace=trace) as agent:
        print(f"\n{AGENT_NAME}: {GREETING}")
        while True:
            try:
                text = await asyncio.to_thread(input, "\nYou: ")
            except (EOFError, KeyboardInterrupt):
                break
            if text.strip().lower() in {"quit", "exit", "bye"}:
                break
            if text.strip():
                await agent.send(text)
        print(f"\n[session ended, estimated cost ${agent.cost_usd:.4f}]")
        if agent.state.card_path:
            print(f"[card saved: {agent.state.card_path}]")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    check() if args.check else asyncio.run(chat(args.quiet))
