# Emotional De-escalation MCP — Agent Instructions

Concise guide for LLM agents using this MCP server. Optimized for minimal context consumption.

## Engine modes

- **host** (default) — tool returns a structured prompt for you (the host LLM) to execute. No API key needed.
- **api** — tool calls LLM API directly, returns parsed results. Requires `ANTHROPIC_API_KEY`.

All analysis tools accept optional `mode` ("host"/"api") to override the default.

## When to use

- User message shows strong emotion (anger, sarcasm, despair) → `emotion_analyze`
- You are drafting a reply to an emotional user → `emotion_de_escalate`
- You need to assess how a conversation is evolving → `emotion_evaluate_dialogue`
- You need to track emotional state across a multi-turn conversation → `session_create` + pass `session_id` to tools

## Analysis Tools

All accept optional `session_id` (for stateful tracking) and `mode` ("host"/"api").

### `emotion_analyze`

Detect emotion and communication style of a message.

```json
{"text": "message to analyze"}
```

Optional: `context` (prior messages), `language_hint` ("en"/"ru"/...), `session_id`, `mode`, `response_format` ("json"/"markdown").

Returns: `emotion` (anger/fear/sadness/happiness/disgust/surprise/neutral), `intensity` (-2..+2), `style_vector` {W,F,P,A,E each -2..+2}, `detected_style`, `triggers`.

### `emotion_de_escalate`

Analyze user emotion + rewrite your draft reply + provide recommendations — all in one call.

```json
{"user_message": "angry message", "draft_response": "your draft reply"}
```

Optional: `target_style` ({warmth,formality,playfulness,assertiveness,expressiveness} — override auto shifts), `dialogue_history`, `preserve_facts` (default true), `session_id`, `mode`, `response_format`.

Auto mode (no `target_style`): analyzes user, shifts W+1 F+1 A-1 E-1 P→0.

Returns: `rewritten_text`, style vectors (user/original/target/achieved), `changes_applied`.

### `emotion_evaluate_dialogue`

Assess dynamics of a full conversation.

```json
{"messages": [{"role": "user", "text": "..."}, {"role": "bot", "text": "..."}]}
```

Roles: `user`, `bot`, `operator`. Min 2 messages, must include at least one `user`.

Optional: `session_id`, `mode`, `response_format`.

Returns: per-message emotion + style vectors, `overall_trend` (escalating/de_escalating/stable_*), `interaction_quality`, `feedback_loop_risk`, `recommendations`.

## Session Management Tools

Sessions enable stateful emotional tracking across conversation turns. Two session modes:
- **adaptive** (default) — mirrors user's style, gradually shifts toward a positive attractor
- **de_escalation** — auto-activates on trigger emotions + high assertiveness/expressiveness

### `session_create`

Create a new tracking session.

```json
{"session_id": "conv-123"}
```

Optional: `config` — custom settings (adaptive_target, adaptive_speed, de_escalation_shifts, de_escalation_emotion_triggers, de_escalation_axis_threshold, timeout_seconds, max_history).

### `session_get`

Retrieve session state.

```json
{"session_id": "conv-123", "include_history": true}
```

Returns: mode, config, turn_count, current_target, history (if requested).

### `session_reset`

Clear session history, optionally keep custom config.

```json
{"session_id": "conv-123", "keep_config": true}
```

### `session_configure`

Update session settings on the fly. Creates session if it doesn't exist.

```json
{"session_id": "conv-123", "config": {"adaptive_speed": 0.5, "adaptive_target": {"warmth": 2, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}}}
```

## Style vector quick reference

5 axes, each integer -2 to +2:

| | -2 | +2 |
|---|---|---|
| **W** Warmth | cold | empathetic |
| **F** Formality | slang | official |
| **P** Playfulness | serious | ironic |
| **A** Assertiveness | meek | forceful |
| **E** Expressiveness | terse | intense |

Patterns: sarcasm = W-2 P+2, aggression = W-2 A+2 E+2, passive-aggression = W-1 P+1 A+1.

## Usage patterns

**Reactive** — user is upset → `emotion_analyze` → if intensity >= 1, run `emotion_de_escalate` on your draft before sending.

**Proactive** — after several exchanges → `emotion_evaluate_dialogue` → check `feedback_loop_risk`; if medium/high, de-escalate next reply.

**One-shot** — skip `emotion_analyze`, go straight to `emotion_de_escalate` with your draft (it includes analysis + de-escalation + recommendations in one call).

**Stateful** — `session_create` at conversation start → pass `session_id` to every tool call → server tracks emotional dynamics and adapts de-escalation strategy automatically (gradual shift toward positive in adaptive mode, stronger corrections when triggered).