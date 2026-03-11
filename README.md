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

## Tools

### `emotion_analyze`

Analyze a message → emotion + style vector + style label.

```json
{
  "text": "ну конечно, ваш замечательный бот мне так помог, спасибо огромное",
  "language_hint": "ru"
}
```

Returns:
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

### `emotion_de_escalate`

Rewrite a draft to match a target style vector.

**Auto mode** (default): analyzes user, applies de-escalation shifts.
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

## Installation & Setup

### Prerequisites

- Python 3.11+
- An Anthropic API key ([get one here](https://console.anthropic.com/settings/keys))

### Quick Start with Claude Code

The easiest way to use this server is with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

**Step 1.** Install the MCP server (one-time setup):

```bash
claude mcp add emotional-deescalation -- uvx emotional-deescalation-mcp
```

> The server uses the `ANTHROPIC_API_KEY` from your environment. If Claude Code is already installed and configured, the key is inherited automatically.
>
> If the key is not in your environment, pass it explicitly:
> ```bash
> claude mcp add emotional-deescalation -e ANTHROPIC_API_KEY=sk-ant-your-key-here -- uvx emotional-deescalation-mcp
> ```

**Step 2.** Start Claude Code as usual:

```bash
claude
```

That's it! The tools `emotion_analyze`, `emotion_de_escalate`, and `emotion_evaluate_dialogue` are now available in your Claude Code session. You can ask Claude to analyze messages, de-escalate responses, or evaluate dialogues.

### Claude Desktop

Add to your Claude Desktop config file (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-your-key-here"
      }
    }
  }
}
```

### Manual / Development Setup

```bash
git clone https://github.com/ilyajob05/emo_bot.git
cd emo_bot
uv sync
export ANTHROPIC_API_KEY=sk-ant-your-key-here
python server.py
```

### Adding to any project via `.mcp.json`

Place an `.mcp.json` file in the root of your project so that Claude Code automatically connects to the server when opened in that directory:

```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"],
      "env": {
        "ANTHROPIC_API_KEY": ""
      }
    }
  }
}
```

> Set the `ANTHROPIC_API_KEY` environment variable in your shell, or fill it in directly in the file.

## Agent integration

For LLM agents connecting via MCP: see **[AGENTS.md](AGENTS.md)** — a compact instruction file designed to minimize context window usage while providing all necessary tool selection and invocation guidance.

## Architecture

```
MCP Client (Claude Desktop, Claude Code, any MCP host)
    │ stdio
    ▼
emotional_deescalation_mcp
    ├── emotion_analyze        → emotion + style vector W/F/P/A/E
    ├── emotion_de_escalate    → rewrite draft to target vector
    └── emotion_evaluate_dialogue → per-message vectors + dynamics
            │
            ▼
      Anthropic Claude API (analysis backend)
```

## License

MIT
