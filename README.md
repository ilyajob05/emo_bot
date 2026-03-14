# Emotional De-escalation MCP Server v2

Universal MCP server for emotional tone analysis and de-escalation using a **5-axis communication style model**.

Based on: [Mistakes to Avoid When Developing Chatbots for User Support](https://medium.com/@ilyajob05/mistakes-to-avoid-when-developing-chatbots-for-user-support-5eefa21256ab)

## 5-Axis Communication Style Model

Every message is characterized by a style vector — 5 independent axes on a discrete scale from **-2** to **+2**:

| Axis | -2 | -1 | 0 | +1 | +2 |
|---|---|---|---|---|---|
| **W** Warmth | cold, detached | cool | neutral | friendly | warm, empathetic |
| **F** Formality | slang, crude | casual | balanced | professional | formal, official |
| **P** Playfulness | dead serious | dry | balanced | light, witty | humorous, ironic |
| **A** Assertiveness | uncertain, meek | tentative | balanced | confident | demanding, forceful |
| **E** Expressiveness | terse, reserved | restrained | balanced | animated | emotional, intense |

### Style Combinations

| Style | W | F | P | A | E |
|---|---|---|---|---|---|
| Sarcasm | -2 | -1 | +2 | 0 | +1 |
| Friendly humor | +1 | -1 | +2 | -1 | 0 |
| Flirtatious | +2 | -2 | +1 | -1 | +1 |
| Business tone | 0 | +2 | -2 | 0 | -1 |
| Aggression | -2 | -2 | -2 | +2 | +2 |
| Desperation | 0 | -1 | -2 | -1 | +2 |
| Passive-aggression | -1 | 0 | +1 | +1 | -1 |

## De-escalation Strategy

Instead of a single coefficient, de-escalation operates **per-axis**:

| Axis | Shift | Rationale |
|---|---|---|
| Warmth | +1 | Increase empathy |
| Formality | +1 | Slightly more professional |
| Playfulness | → 0 | Reduce sarcasm risk |
| Assertiveness | -1 | Reduce pressure |
| Expressiveness | -1 | Calm down intensity |

This breaks the positive feedback loop by ensuring the bot's response vector is always shifted toward a more constructive zone.

## Engine Modes

The server supports two execution modes, controlled via `EMOTION_MCP_MODE` env var or per-call `mode` parameter:

| Mode | How it works | API key required | Cost |
|------|-------------|------------------|------|
| **host** (default) | Tool returns a structured prompt → host LLM (Claude Desktop/Code/LM Studio) executes it | No | Free |
| **api** | Tool calls Anthropic API directly, returns parsed results | Yes | Paid |

In **host mode**, each tool returns a single self-contained prompt with the full 5-axis model, analysis rules, session context, and task-specific instructions. The host LLM executes everything in one pass.

In **api mode**, the server makes its own LLM calls and returns structured results (JSON or Markdown).

To switch to API mode:
```bash
# Via environment variable
export EMOTION_MCP_MODE=api

# Or per-call parameter
{"text": "...", "mode": "api"}
```

## Tools

### Analysis Tools

All analysis tools accept optional parameters:
- `session_id` — for stateful emotional tracking across turns
- `mode` — `"host"` or `"api"` (overrides env var)

### `emotion_analyze`

Analyze a message → emotion + style vector + style label.

```json
{
  "text": "ну конечно, ваш замечательный бот мне так помог, спасибо огромное",
  "language_hint": "ru"
}
```

Returns (API mode):
```json
{
  "emotion": "anger",
  "intensity": 1,
  "style_vector": {"warmth": -2, "formality": -1, "playfulness": 2, "assertiveness": 0, "expressiveness": 1},
  "detected_style": "sarcasm",
  "explanation": "...",
  "triggers": ["ну конечно", "замечательный", "спасибо огромное"]
}
```

In host mode, returns a structured prompt for the host LLM to produce the same analysis.

### `emotion_de_escalate`

Analyze user emotion + rewrite a draft + provide recommendations — all in one call.

**Auto mode** (default): analyzes user's style, applies de-escalation shifts.
**Override mode**: pass `target_style` explicitly.

```json
{
  "user_message": "what the hell is wrong with the delivery?!",
  "draft_response": "Your order should arrive by April 10.",
  "target_style": {"warmth": 1, "formality": 1, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
}
```

### `emotion_evaluate_dialogue`

Evaluate full dialogue → per-message vectors + trend + feedback loop risk.

```json
{
  "messages": [
    {"role": "user", "text": "hello"},
    {"role": "bot", "text": "Hello! How can I help?"},
    {"role": "user", "text": "what the hell is wrong with the delivery?!"},
    {"role": "bot", "text": "Your order #3756 arrives April 10."},
    {"role": "user", "text": "ok I guess I'll wait"}
  ],
  "response_format": "markdown"
}
```

Returns a table:

| # | Role | Emotion | W | F | P | A | E | Style |
|---|------|---------|---|---|---|---|---|-------|
| 1 | user | neutral | 0 | -1 | 0 | 0 | 0 | casual |
| 2 | bot | neutral | +1 | 0 | 0 | 0 | 0 | friendly |
| 3 | user | anger | -2 | -2 | -2 | +2 | +2 | aggressive |
| 4 | bot | neutral | 0 | +1 | -1 | 0 | -1 | business |
| 5 | user | neutral | 0 | -1 | 0 | -1 | 0 | resigned |

### Session Management Tools

Sessions enable stateful emotional tracking across conversation turns.

- **`session_create`** — create a session with optional custom config (target attractor, shift speed, thresholds)
- **`session_get`** — retrieve session state: mode, config, turn count, optionally full history
- **`session_reset`** — clear history, optionally keep custom config
- **`session_configure`** — update session settings on the fly

**Session modes:**
- **Adaptive** (default) — mirrors user's style, gradually shifts toward a positive attractor each turn
- **De-escalation** — auto-activates on trigger emotions (anger/disgust/fear) + high assertiveness or expressiveness; applies stronger corrective shifts; reverts to adaptive after cooldown

## Installation & Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- An Anthropic API key ([get one here](https://console.anthropic.com/settings/keys)) — **only required for API mode**. Host mode (default) works without it.

> **Important (macOS / Linux):** GUI applications like Claude Desktop don't inherit your shell PATH, so `uvx` may not be found. Create a symlink to make it available system-wide:
> ```bash
> # Find where uvx and uv is installed
> which uvx
> # Create a symlink (example, adjust the source path if yours differs)
> sudo ln -sf ~/.local/bin/uvx /usr/local/bin/uvx
> which uv
> # Create a symlink (example, adjust the source path if yours differs)
> sudo ln -sf ~/.local/bin/uv /usr/local/bin/uv
> ```

### Quick Start with Claude Code

The easiest way to use this server is with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

**Step 1.** Install the MCP server (one-time setup):

```bash
claude mcp add emotional-deescalation -- uvx emotional-deescalation-mcp
```

> In **host mode** (default), no API key is needed — Claude Code itself performs the analysis using the structured prompts from the server.
>
> For **API mode** (server makes its own LLM calls), pass the key:
> ```bash
> claude mcp add emotional-deescalation -e ANTHROPIC_API_KEY=sk-ant-your-key-here -e EMOTION_MCP_MODE=api -- uvx emotional-deescalation-mcp
> ```

**Step 2.** Start Claude Code as usual:

```bash
claude
```

That's it! The tools are now available in your Claude Code session. You can ask Claude to analyze messages, de-escalate responses, or evaluate dialogues.

### Claude Desktop

Add to your Claude Desktop config file (`claude_desktop_config.json`):

**Host mode (default, no API key needed):**
```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"]
    }
  }
}
```

**API mode (server makes its own LLM calls):**
```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-your-key-here",
        "EMOTION_MCP_MODE": "api"
      }
    }
  }
}
```

> **If `uvx` is not found:** use the full path instead of `"uvx"` (e.g. `"/Users/you/.local/bin/uvx"`), or create a symlink as described in [Prerequisites](#prerequisites).

### Manual / Development Setup

```bash
git clone https://github.com/ilyajob05/emo_bot.git
cd emo_bot
uv sync
python server.py                          # host mode (default)
# or for API mode:
# export ANTHROPIC_API_KEY=sk-ant-...
# EMOTION_MCP_MODE=api python server.py
```

### Adding to any project via `.mcp.json`

Place an `.mcp.json` file in the root of your project so that Claude Code automatically connects to the server when opened in that directory:

```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"]
    }
  }
}
```

> For API mode, add `"env": {"ANTHROPIC_API_KEY": "...", "EMOTION_MCP_MODE": "api"}` to the config.

## Agent integration

For LLM agents connecting via MCP: see **[AGENTS.md](AGENTS.md)** — a compact instruction file designed to minimize context window usage while providing all necessary tool selection and invocation guidance.

## Architecture

```
MCP Client (Claude Desktop, Claude Code, LM Studio, any MCP host)
    │ stdio
    ▼
emotional_deescalation_mcp
    ├── emotion_analyze             ─┐
    ├── emotion_de_escalate          ├── Analysis tools (accept mode + session_id)
    ├── emotion_evaluate_dialogue   ─┘
    ├── session_create              ─┐
    ├── session_get                  ├── Session management
    ├── session_reset                │
    └── session_configure           ─┘
            │
            ├── HOST mode: returns structured prompt → host LLM executes (free)
            │
            └── API mode: calls LLM directly → Anthropic Claude API (paid)
```

## License

MIT
