"""Settings and the connection to OpenRouter.

The Claude Agent SDK runs Claude Code under the hood, and Claude Code talks
Anthropic's API format. OpenRouter offers an Anthropic-compatible endpoint, so
we point Claude Code at it with three environment variables:

    ANTHROPIC_BASE_URL   = https://openrouter.ai/api   (no /v1)
    ANTHROPIC_AUTH_TOKEN = your OpenRouter key          (sent as a Bearer token)
    ANTHROPIC_API_KEY    = ""                           (must be empty)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

load_dotenv(ROOT / ".env")

# The agent's name. Change it here and it changes everywhere: the greeting, the agent's
# instructions, the chat, and the pages. The app itself stays "Just in Case".
AGENT_NAME = "Haven"

MODEL = os.getenv("JIC_MODEL", "anthropic/claude-haiku-5.5")
MAX_BUDGET_USD = float(os.getenv("JIC_MAX_BUDGET_USD", "0.50"))

# A directory entry older than this is flagged "call to confirm".
STALE_AFTER_DAYS = 365
# Plans stay small: at most this many people across the whole plan.
MAX_HELPERS = 3
# Months until the card says "review this plan".
REVIEW_AFTER_MONTHS = 4


def runtime_env() -> dict[str, str]:
    """Environment variables handed to the Claude Code process the SDK starts."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or "paste-your-key" in key:
        raise SystemExit(
            "No OpenRouter key found. Copy .env.example to .env and paste your key."
        )
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api").rstrip("/")
    if base.endswith("/v1"):
        raise SystemExit(
            "OPENROUTER_BASE_URL ends in /v1. The Agent SDK needs "
            "https://openrouter.ai/api (the Anthropic-compatible endpoint)."
        )
    return {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_API_KEY": "",
        # Claude Code sometimes uses a small helper model; keep it on our model.
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODEL,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": MODEL,
        # Skip telemetry and other background calls we do not need.
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
