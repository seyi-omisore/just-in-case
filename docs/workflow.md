# Workflow

GitHub renders this diagram automatically. Leya can redraw it in Canva for the deck.

```mermaid
flowchart TD
    A[User describes what's coming up] --> B[Agent picks needs from a fixed list]
    B --> C{{Checkpoint 1: user confirms needs}}
    C --> D[Agent asks: comfortable asking a friend?]
    D --> E[For each need: decision ladder]
    E --> F1[1. Service from verified directory<br/>free first, waitlists flagged]
    F1 -->|none fits| F2[2. Simple preparation]
    F2 -->|still needed| F3{People allowed?}
    F3 -->|yes| F4[3. One small role for a chosen person]
    F3 -->|no or burden worry| F5[4. Blank + point to Council on Aging / Mass211]
    F4 --> G{{Checkpoint 2: user approves the note<br/>user sends it from their own phone or email}}
    F1 --> H
    F2 --> H
    F5 --> H
    G --> H{{Checkpoint 3: user approves the card}}
    H --> I[Printed card on the fridge<br/>911 line, contacts, plan, review date]
```

## Agent responsibilities
- Turn a conversation into a short list of needs.
- Search the directory and preparations for each need.
- Choose a route for each need following the ladder.
- Draft at most one note, only when allowed.
- Assemble the card from what the user typed.

## Human-in-the-loop decisions
| Checkpoint | What the user decides | Enforced by |
|---|---|---|
| 1 | Which needs to plan for | `confirm_needs` is gated; planning is blocked until confirmed |
| 2 | Whether a note is right, and whether to send it | `draft_helper_note` is gated; no send tool exists |
| 3 | What goes on the card | `make_fridge_card` is gated |

## Architecture
Single agent with eight custom tools in an in-process MCP server. A single agent
is the right fit here: the steps depend on each other and on one conversation,
and splitting them would add cost and failure points without adding value.
