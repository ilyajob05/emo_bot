[Русская версия](CONTRIBUTING.ru.md)

# Contributing

We welcome contributions! Whether it's a bug fix, a new pattern detector, a new strategy, or a translation — every PR matters.

## Quick Start

```bash
# 1. Fork and clone
git clone https://github.com/<your-username>/emo_bot.git
cd emo_bot

# 2. Install dependencies
uv sync --extra dev

# 3. Install spaCy model
uv run python -m spacy download ru_core_news_sm

# 4. Run tests (make sure everything passes)
uv run pytest tests/ -v

# 5. Create a branch
git checkout -b feature/my-awesome-feature
```

## What Can I Contribute?

### Easy (Good First Issues)

- **New keywords** — add escalation/legal/churn keywords in `config/patterns.toml` for your language or domain
- **Translations** — translate anti-pattern messages and strategy notes to new languages
- **Documentation** — fix typos, improve examples, add usage scenarios

### Medium

- **New pattern detector** — detect a new problematic dialogue pattern (see guide below)
- **New strategy** — add a strategy for a pattern that doesn't have one yet
- **Tests** — add edge case tests, especially for multilingual scenarios

### Advanced

- **New MCP tool** — add a new tool to the server (e.g. `dialogue_summarize`, `response_compose`)
- **NLP improvements** — better emotion detection, new embedding models
- **Integration examples** — example MCP servers for specific databases or CRM systems

## Adding a New Pattern Detector

This is the most common and valuable contribution. Here's how:

### 1. Add keywords to `config/patterns.toml`

If your detector uses keyword matching, add a new TOML section:

```toml
[your_pattern]
lemmas_ru = ["ключ1", "ключ2"]        # spaCy lemma forms (one covers all inflections)
substrings_ru = ["многословная фраза"] # exact substring match, case-insensitive
lemmas_en = ["keyword1", "keyword2"]
substrings_en = ["multi-word phrase"]
```

Override the config path via `PATTERNS_CONFIG` env var to use a custom file.

### 2. Add your detector function in `src/pattern_detector.py`

```python
def detect_your_pattern(
    messages: list[DialogueMessage],
    **kwargs,
) -> DetectedPattern | None:
    """Detect <describe what it detects>."""
    user_msgs = [m for m in messages if m.role == "user"]
    # Your detection logic here
    # Return None if pattern not found
    if not detected:
        return None
    return DetectedPattern(
        pattern_type="your_pattern",
        severity="warning",  # "info", "warning", or "critical"
        confidence=0.8,
        evidence=["specific evidence from the dialogue"],
        details={"key": "value"},
    )
```

### 3. Register it in `detect_all_patterns()`

```python
# In detect_all_patterns(), add your detector call:
result = detect_your_pattern(messages)
if result:
    patterns.append(result)
```

### 4. Add a strategy builder in `src/strategy_rules.py`

```python
def _build_your_pattern_strategy(pattern, available_actions, language):
    # Return a StrategyResult
    ...

# Register in _STRATEGY_BUILDERS:
_STRATEGY_BUILDERS["your_pattern"] = _build_your_pattern_strategy

# Set priority in _PATTERN_PRIORITY:
_PATTERN_PRIORITY["your_pattern"] = 5  # adjust based on urgency
```

### 5. Add tests

```python
# In tests/test_pattern_detector.py:
class TestYourPattern:
    def test_detects_pattern(self):
        messages = [...]
        result = detect_your_pattern(messages)
        assert result is not None
        assert result.pattern_type == "your_pattern"

    def test_no_false_positive(self):
        messages = [...]  # normal dialogue
        result = detect_your_pattern(messages)
        assert result is None
```

### 6. Run all tests

```bash
uv run pytest tests/ -v
```

## Project Structure (Key Files)

```
config/
└── patterns.toml         ← keyword databases & thresholds (customize here)

src/
├── pattern_detector.py   ← pattern detection (add detectors here)
├── pattern_config.py     ← TOML config loader
├── strategy_rules.py     ← strategy mapping (add strategies here)
├── models.py             ← Pydantic models
├── nlp/
│   ├── spacy_singleton.py  ← spaCy loader + text utilities
│   └── config.py           ← environment variables
└── tools/
    └── strategy_suggest.py ← MCP tool wrapper

server.py                 ← main MCP server (all tools registered here)
tests/                    ← all tests
```

## Code Style

- Comments in English
- Keep it simple — no over-engineering
- All pattern detectors must be deterministic (no LLM calls, no randomness)
- Use spaCy lemmatization for keyword matching (see `contains_any_lemma()` in `src/nlp/spacy_singleton.py`)
- Add both Russian and English keywords where applicable

## Submitting a PR

1. Make sure all tests pass: `uv run pytest tests/ -v`
2. Keep your PR focused — one feature/fix per PR
3. Write a short description of what and why
4. If adding a new pattern: include test cases with real-world examples

That's it. No complicated setup, no CLA, no lengthy review process. If your code works and tests pass — it gets merged.
