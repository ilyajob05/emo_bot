# Emotional De-escalation MCP Server v2

[Русская версия](README.ru.md)

MCP server for **strategic dialogue management** and emotional de-escalation in customer support bots.

The core problem: support bots get stuck in loops — asking for an order number 5 times, repeating the same apology, ignoring escalation signals. This server detects these patterns and tells the bot **what to do next** — change strategy, escalate to a human operator, or stop repeating itself.

**Key capability: deterministic escalation detection.** The `strategy_suggest` tool identifies 7 problem patterns (repeated questions, legal threats, churn signals, requests for a human, emotional escalation, no progress, repeated contacts) and recommends concrete actions — including when to escalate to a human operator. Works without LLM calls, offline, instant.

Based on: [Mistakes to Avoid When Developing Chatbots for User Support](https://medium.com/@ilyajob05/mistakes-to-avoid-when-developing-chatbots-for-user-support-5eefa21256ab)

## Strategic Dialogue Management

The MVP covers 80% of problematic support scenarios **without any LLM calls**. The deterministic engine uses spaCy lemmatization and pattern matching to detect problems and recommend strategies:

### Detected Patterns

| Pattern | What it detects | Escalation |
|---------|----------------|------------|
| **repeated_question** | Bot asked the same question 2+ times | After 4 repetitions |
| **legal_threat** | User mentions lawsuits, regulators, lawyers | Immediate |
| **human_request** | User explicitly asks for a live agent | Immediate |
| **churn_signal** | User threatens to cancel, leave, or demands refund | After 1 more turn |
| **emotion_escalation** | User's emotional intensity is increasing | After 2 more turns |
| **no_progress** | Both sides repeating themselves, dialogue stuck | After 4 stagnant turns |
| **repeated_contact** | User's Nth contact today (via metadata) | After 3rd contact |

### Escalation to Human Operator

The server provides clear, actionable escalation signals:

- **`should_escalate_now: true`** — transfer immediately (legal threats, explicit human requests, 4+ repeated questions, 3+ contacts today)
- **`escalate_after_n_more_turns: N`** — escalate if not resolved within N turns
- **Priority system** ensures the most critical pattern drives the strategy: legal_threat > human_request > repeated_contact > repeated_question > no_progress > churn_signal > emotion_escalation

### Anti-Patterns

The engine tracks what the bot has already said and generates "do NOT" rules:
- "Do NOT ask for order number again" (if asked 2+ times)
- "Do NOT use 'I understand your frustration' — already said 2 times"
- "Do NOT argue with the customer about their rights" (on legal threats)
- "Do NOT say 'calm down'" (on emotional escalation)

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

### Strategic Tools

#### `strategy_suggest`

**Call this BEFORE composing a reply.** Analyzes dialogue patterns and recommends what to do next. Fully deterministic — no LLM, works offline, instant.

```json
{
  "dialogue_history": [
    {"role": "user", "text": "Где мой заказ?"},
    {"role": "bot", "text": "Уточните номер заказа."},
    {"role": "user", "text": "Я уже говорил! Уточните по телефону!"},
    {"role": "bot", "text": "Подскажите номер заказа, пожалуйста."},
    {"role": "user", "text": "Да что с вами не так?! Позовите оператора!"}
  ],
  "available_actions": ["lookup_by_phone", "escalate_to_human"],
  "user_metadata": {"total_contacts_today": 2},
  "language": "ru"
}
```

Example response:
```json
{
  "recommended_strategy": "comply_with_human_request",
  "reasoning": "Пользователь явно запросил живого оператора. Попытки продолжить диалог ботом ухудшат ситуацию.",
  "action_sequence": [
    {"action": "escalate_to_human", "priority": "required", "note": "Пользователь явно попросил оператора — выполнить без промедления."}
  ],
  "anti_patterns": [
    "НЕ спрашивать номер заказа снова — бот уже спрашивал 2 раза",
    "НЕ говорить 'успокойтесь' или 'не нервничайте'"
  ],
  "escalation": {
    "should_escalate_now": true,
    "escalate_after_n_more_turns": null,
    "reason": "Пользователь явно запросил живого оператора"
  },
  "detected_patterns": ["human_request", "repeated_question", "emotion_escalation"]
}
```

**Strategies by pattern:**

| Pattern | Strategy | Key action |
|---------|----------|------------|
| repeated_question | alternative_identification | Try phone/email lookup instead |
| legal_threat | immediate_supervisor_escalation | Transfer to supervisor now |
| human_request | comply_with_human_request | Transfer to operator now |
| churn_signal | retention | Offer compensation, connect manager |
| emotion_escalation | de_escalation | Slow down, validate emotions |
| no_progress | break_deadlock | Summarize and escalate |
| repeated_contact | priority_handling | Acknowledge, prioritize |

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

**If You Need Update MCP**
```bash
uv cache clean emotional-deescalation-mcp
claude mcp remove emotional-deescalation
claude mcp add emotional-deescalation -- uvx emotional-deescalation-mcp@latest
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

## Production Integration

For connecting to a corporate system (REST API, database, escalation service): see **[Integration Guide](docs/integration_guide.md)** — covers bot response format, escalation signals, third-party MCP server setup (PostgreSQL, custom order management), and step-by-step deployment instructions.

## Architecture

```
MCP Client (Claude Desktop, Claude Code, LM Studio, any MCP host)
    │ stdio
    ▼
emotional_deescalation_mcp
    ├── strategy_suggest             ── Strategic tool (deterministic, no LLM)
    │       │
    │       ├── Pattern detection (spaCy lemmatization)
    │       └── Strategy rules engine
    │
    ├── emotion_analyze             ─┐
    ├── emotion_de_escalate          ├── Analysis tools (accept mode + session_id)
    ├── emotion_evaluate_dialogue   ─┘
    │       │
    │       ├── HOST mode: structured prompt → host LLM (free)
    │       └── API mode: Anthropic Claude API (paid)
    │
    ├── session_create              ─┐
    ├── session_get                  ├── Session management
    ├── session_reset                │
    └── session_configure           ─┘

    Optional: External NLP Service (nlp_service/)
    ├── POST /embed    ── sentence embeddings (multilingual-e5-base)
    ├── POST /emotion  ── emotion classification (RU + EN models, routed by language)
    └── GET  /health
```

## NLP Service (Optional)

An external FastAPI service for sentence embeddings and emotion classification. The MCP server works without it (falls back to spaCy-only detection), but the NLP service improves quality via semantic similarity and ML-based emotion classification.

**Emotion models (two specialized, routed by language):**
- **Russian:** `cointegrated/rubert-tiny2-cedr-emotion-detection` (~30MB, 6 Ekman emotions, F1=0.83)
- **English:** `j-hartmann/emotion-english-distilroberta-base` (~82MB, 7 Ekman emotions)

**Embedding model:** `intfloat/multilingual-e5-base` (RU + EN sentence embeddings)

```bash
# Run with Docker
cd nlp_service
docker build -t nlp-service .
docker run -p 8100:8100 nlp-service

# Or override models at build time
docker build \
  --build-arg NLP_EMOTION_MODEL_RU=your/ru-model \
  --build-arg NLP_EMOTION_MODEL_EN=your/en-model \
  -t nlp-service .
```

Configure via environment variables:
- `NLP_SERVICE_URL` — default `http://localhost:8100`
- `NLP_EMOTION_MODEL_RU`, `NLP_EMOTION_MODEL_EN` — override emotion models
- `NLP_EMBED_MODEL` — override embedding model

## Using with Context7

[Context7](https://context7.com) provides up-to-date documentation for LLMs and AI code editors. Once this library is indexed, any LLM agent with Context7 MCP can instantly access the full documentation — tools, parameters, usage patterns, and integration guides.

See **[Context7 Setup Guide](docs/context7-setup.md)** for configuration instructions.

## Contributing

Want to add a new pattern detector, strategy, or language support? See **[CONTRIBUTING.md](CONTRIBUTING.md)** — quick setup, clear guide for adding new detectors, no red tape.

## License

MIT
