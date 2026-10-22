"""The agent loop: connects the model, the tools, and the human approval gate.

Flow of one turn:
  user types -> client.query() -> model thinks -> model calls a tool
     -> approval_gate() decides: allow, ask the user, or deny
     -> tool runs (rules checked in Toolbox) -> result back to the model
     -> ... repeat until the model replies with text -> we print it.
"""

import asyncio
import json
from typing import Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from .config import MAX_BUDGET_USD, MODEL, runtime_env
from .directory import Directory
from .prompts import SYSTEM_PROMPT
from .safeguards import check_helper_note
from .state import NEEDS, PlanState
from .tools import GATED_TOOLS, PREFIX, Toolbox, build_server

# An approver gets (tool_name, input) and returns True to allow.
Approver = Callable[[str, dict], Awaitable[bool]]


def describe(short: str, data: dict) -> str:
    """What the user sees before approving a checkpoint."""
    if short == "confirm_needs":
        return "Plan for these needs:\n" + "\n".join(f"  - {NEEDS.get(n, n)}" for n in data["needs"])
    if short == "draft_helper_note":
        return (f"Note for {', '.join(data['helper_names'])} (role: {data['role']}):\n\n"
                f"{data['note_text']}\n\n(This will NOT be sent. You send it yourself if you like.)")
    if short == "make_fridge_card":
        return "Create your card with:\n" + json.dumps(data, indent=2)
    return json.dumps(data, indent=2)


async def terminal_approver(short: str, data: dict) -> bool:
    print("\n" + "=" * 60 + "\nPLEASE REVIEW\n" + describe(short, data) + "\n" + "=" * 60)
    answer = await asyncio.to_thread(input, "Approve? (y/n): ")
    return answer.strip().lower().startswith("y")


def make_gate(approver: Approver, trace=print):
    async def approval_gate(tool_name: str, input_data: dict, context):
        # 1. Only our own tools. Anything else (files, shell, web) is refused.
        if not tool_name.startswith(PREFIX):
            trace(f"  [rule]  refused non-Just-in-Case tool: {tool_name}")
            return PermissionResultDeny(message="Only Just in Case tools are available.")
        short = tool_name.removeprefix(PREFIX)
        if short not in GATED_TOOLS:
            return PermissionResultAllow(updated_input=input_data)
        # 2. Pre-check the note so the user is never asked to approve a bad draft.
        if short == "draft_helper_note":
            problem = check_helper_note(input_data.get("note_text", ""))
            if problem:
                trace(f"  [rule]  note sent back for rewrite: {problem}")
                return PermissionResultDeny(message=f"RULE: {problem}")
        # 3. Human checkpoint.
        if await approver(short, input_data):
            return PermissionResultAllow(updated_input=input_data)
        trace(f"  [human] user declined {short}")
        return PermissionResultDeny(
            message="The user did not approve this. Ask what they would like to change."
        )

    return approval_gate


class JustInCaseAgent:
    def __init__(self, approver: Approver = terminal_approver, town: str = "Boston",
                 directory: Directory | None = None, trace=print, model: str | None = None,
                 on_tool=None, out_dir=None):
        self.state = PlanState(town=town)
        self.box = Toolbox(self.state, directory or Directory(),
                           **({"out_dir": out_dir} if out_dir else {}))
        self.trace = trace
        # Optional hook: on_tool(short_name, input_dict). The website uses it for
        # the live "what Just in Case is doing" feed.
        self.on_tool = on_tool
        server, tool_names = build_server(self.box, trace=trace)
        gated = {PREFIX + t for t in GATED_TOOLS}
        self.options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,          # replaces Claude Code's coding prompt
            model=model or MODEL,
            mcp_servers={"jic": server},
            tools=[],                              # turn OFF built-ins (Bash, Read, Web...)
            allowed_tools=[n for n in tool_names if n not in gated],  # auto-approved
            can_use_tool=make_gate(approver, trace),  # everything else comes here
            setting_sources=[],                    # ignore CLAUDE.md, plugins, user settings
            env=runtime_env(),                     # route to OpenRouter
            max_turns=40,
            max_budget_usd=MAX_BUDGET_USD,
        )
        self.cost_usd = 0.0
        self.transcript: list[tuple[str, str]] = []

    async def __aenter__(self):
        self.client = ClaudeSDKClient(options=self.options)
        await self.client.connect()
        return self

    async def __aexit__(self, *exc):
        await self.client.disconnect()

    async def send(self, user_text: str, on_text=print) -> str:
        """Send one user message and return the agent's full text reply."""
        self.transcript.append(("user", user_text))
        await self.client.query(user_text)
        reply_parts = []
        async for msg in self.client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        reply_parts.append(block.text)
                        on_text(f"\nJust in Case: {block.text}")
                    elif isinstance(block, ToolUseBlock):
                        short = block.name.removeprefix(PREFIX)
                        self.trace(f"  [tool]  {short}")
                        if self.on_tool:
                            self.on_tool(short, block.input or {})
            elif isinstance(msg, ResultMessage):
                # Estimate only (priced at Anthropic list rates). The OpenRouter
                # Activity page is the real bill.
                self.cost_usd = max(self.cost_usd, msg.total_cost_usd or 0.0)
                if msg.is_error:
                    on_text(f"\n[run ended: {msg.subtype}]")
        reply = "\n".join(reply_parts)
        self.transcript.append(("agent", reply))
        return reply
