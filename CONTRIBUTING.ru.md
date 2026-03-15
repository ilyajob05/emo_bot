[English version](CONTRIBUTING.md)

# Участие в разработке

Мы приветствуем вклад каждого! Будь то исправление бага, новый детектор паттернов, новая стратегия или перевод — каждый PR важен.

## Быстрый старт

```bash
# 1. Форкните и клонируйте
git clone https://github.com/<your-username>/emo_bot.git
cd emo_bot

# 2. Установите зависимости
uv sync --extra dev

# 3. Установите модель spaCy
uv run python -m spacy download ru_core_news_sm

# 4. Запустите тесты (убедитесь, что всё проходит)
uv run pytest tests/ -v

# 5. Создайте ветку
git checkout -b feature/my-awesome-feature
```

## Что можно сделать?

### Простое (для начала)

- **Новые ключевые слова** — добавьте слова в `config/patterns.toml` для вашего языка или домена
- **Переводы** — переведите сообщения anti-pattern и заметки стратегий на новые языки
- **Документация** — исправьте опечатки, улучшите примеры, добавьте сценарии использования

### Средняя сложность

- **Новый детектор паттернов** — обнаружение нового проблемного паттерна диалога (см. инструкцию ниже)
- **Новая стратегия** — добавьте стратегию для паттерна, у которого её ещё нет
- **Тесты** — добавьте тесты на граничные случаи, особенно для мультиязычных сценариев

### Продвинутое

- **Новый MCP-инструмент** — добавьте новый инструмент на сервер (например `dialogue_summarize`, `response_compose`)
- **Улучшения NLP** — улучшение определения эмоций, новые модели эмбеддингов
- **Примеры интеграции** — примеры MCP-серверов для конкретных баз данных или CRM-систем

## Добавление нового детектора паттернов

Это самый частый и ценный вклад. Вот как это сделать:

### 1. Добавьте ключевые слова в `config/patterns.toml`

Если ваш детектор использует поиск по ключевым словам, добавьте новую секцию в TOML:

```toml
[your_pattern]
lemmas_ru = ["ключ1", "ключ2"]        # леммы spaCy (одна форма покрывает все склонения)
substrings_ru = ["многословная фраза"] # точное совпадение подстроки, без учёта регистра
lemmas_en = ["keyword1", "keyword2"]
substrings_en = ["multi-word phrase"]
```

Путь к конфигу можно переопределить через переменную окружения `PATTERNS_CONFIG`.

### 2. Добавьте функцию детектора в `src/pattern_detector.py`

```python
def detect_your_pattern(
    messages: list[DialogueMessage],
    **kwargs,
) -> DetectedPattern | None:
    """Detect <описание того, что детектирует>."""
    user_msgs = [m for m in messages if m.role == "user"]
    # Ваша логика обнаружения
    # Вернуть None если паттерн не найден
    if not detected:
        return None
    return DetectedPattern(
        pattern_type="your_pattern",
        severity="warning",  # "info", "warning" или "critical"
        confidence=0.8,
        evidence=["конкретные доказательства из диалога"],
        details={"key": "value"},
    )
```

### 3. Зарегистрируйте в `detect_all_patterns()`

```python
# В detect_all_patterns() добавьте вызов вашего детектора:
result = detect_your_pattern(messages)
if result:
    patterns.append(result)
```

### 4. Добавьте построитель стратегии в `src/strategy_rules.py`

```python
def _build_your_pattern_strategy(pattern, available_actions, language):
    # Верните StrategyResult
    ...

# Зарегистрируйте в _STRATEGY_BUILDERS:
_STRATEGY_BUILDERS["your_pattern"] = _build_your_pattern_strategy

# Установите приоритет в _PATTERN_PRIORITY:
_PATTERN_PRIORITY["your_pattern"] = 5  # настройте по срочности
```

### 5. Добавьте тесты

```python
# В tests/test_pattern_detector.py:
class TestYourPattern:
    def test_detects_pattern(self):
        messages = [...]
        result = detect_your_pattern(messages)
        assert result is not None
        assert result.pattern_type == "your_pattern"

    def test_no_false_positive(self):
        messages = [...]  # обычный диалог
        result = detect_your_pattern(messages)
        assert result is None
```

### 6. Запустите все тесты

```bash
uv run pytest tests/ -v
```

## Структура проекта (ключевые файлы)

```
config/
└── patterns.toml         ← база ключевых слов и пороги (настраивать здесь)

src/
├── pattern_detector.py   ← детекторы паттернов (добавлять сюда)
├── pattern_config.py     ← загрузчик TOML-конфигурации
├── strategy_rules.py     ← маппинг стратегий (добавлять сюда)
├── models.py             ← Pydantic-модели
├── nlp/
│   ├── spacy_singleton.py  ← загрузчик spaCy + утилиты для текста
│   └── config.py           ← переменные окружения
└── tools/
    └── strategy_suggest.py ← обёртка MCP-инструмента

server.py                 ← основной MCP-сервер (все инструменты зарегистрированы здесь)
tests/                    ← все тесты
```

## Стиль кода

- Комментарии в коде — на английском
- Простота — без overengineering
- Все детекторы паттернов должны быть детерминированными (без LLM-вызовов, без случайности)
- Используйте лемматизацию spaCy для поиска ключевых слов (см. `contains_any_lemma()` в `src/nlp/spacy_singleton.py`)
- Добавляйте ключевые слова на русском и английском где применимо

## Отправка PR

1. Убедитесь, что все тесты проходят: `uv run pytest tests/ -v`
2. Один PR — одна фича/фикс
3. Напишите краткое описание что и зачем
4. Если добавляете новый паттерн — включите тесты с примерами из реальных диалогов

Всё. Без сложной настройки, без CLA, без долгого ревью. Если код работает и тесты проходят — мержим.
