# Emotional De-escalation MCP — Agent Instructions

Concise guide for LLM agents using this MCP server. Optimized for minimal context consumption.

## When to use

- User message shows strong emotion (anger, sarcasm, despair) → `emotion_analyze`
- You are drafting a reply to an emotional user → `emotion_de_escalate`
- You need to assess how a conversation is evolving → `emotion_evaluate_dialogue`

## Tools

### `emotion_analyze`

Detect emotion and communication style of a message.

```json
{"text": "message to analyze"}
```

Optional: `context` (prior messages), `language_hint` ("en"/"ru"/...), `response_format` ("json"/"markdown").

Returns: `emotion` (anger/fear/sadness/happiness/disgust/surprise/neutral), `intensity` (-2..+2), `style_vector` {W,F,P,A,E each -2..+2}, `detected_style`, `triggers`.

### `emotion_de_escalate`

Rewrite your draft reply to de-escalate conflict.

```json
{"user_message": "angry message", "draft_response": "your draft reply"}
```

Optional: `target_style` ({warmth,formality,playfulness,assertiveness,expressiveness} — override auto shifts), `dialogue_history`, `preserve_facts` (default true), `response_format`.

Auto mode (no `target_style`): analyzes user, shifts W+1 F+1 A-1 E-1 P→0.

Returns: `rewritten_text`, style vectors (user/original/target/achieved), `changes_applied`.

### `emotion_evaluate_dialogue`

Assess dynamics of a full conversation.

```json
{"messages": [{"role": "user", "text": "..."}, {"role": "bot", "text": "..."}]}
```

Roles: `user`, `bot`, `operator`. Min 2 messages, must include at least one `user`.

Optional: `response_format`.

Returns: per-message emotion + style vectors, `overall_trend` (escalating/de_escalating/stable_*), `interaction_quality`, `feedback_loop_risk`, `recommendations`.

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

**One-shot** — skip `emotion_analyze`, go straight to `emotion_de_escalate` with your draft (it auto-analyzes the user message internally).