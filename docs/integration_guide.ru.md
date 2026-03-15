[English version](integration_guide.md)

# Руководство по интеграции: подключение к корпоративной системе

Документ описывает, как интегрировать MCP-бота поддержки в продакшн-среду с корпоративным REST API.

## Текущий режим

Бот работает в **демо-режиме** — общается с пользователем напрямую через MCP-хост (Claude Desktop, Claude Code и т.д.). В продакшне бот находится между пользователем и корпоративным бэкендом, обмениваясь структурированными сообщениями через REST API.

## Обзор архитектуры

```
Демо-режим:
  Пользователь ←→ MCP Host (Claude) ←→ MCP Server (strategy_suggest, emotion tools)

Продакшн-режим:
  Пользователь ←→ Чат-платформа (web/mobile/telegram)
                    ↕
                  API Gateway
                    ↕
                  Оркестратор бота
                    ├── MCP Server (strategy_suggest, emotion tools)
                    ├── Сервис заказов REST API (order_lookup, статус доставки)
                    └── Сервис эскалации (передача оператору)
```

## Формат ответа бота

В продакшне бот должен возвращать **структурированный ответ** (не просто текст), чтобы корпоративная система могла его распарсить и выполнить нужные действия.

### Схема ответа

```json
{
  "message": {
    "text": "Ваш заказ 764654123 в пути. Ожидаемая доставка: 16 марта, 14:00–18:00.",
    "language": "ru"
  },
  "escalation": {
    "required": false,
    "immediate": false,
    "after_n_turns": null,
    "reason": null,
    "operator_context": null
  },
  "actions_taken": [
    {"tool": "strategy_suggest", "result": "continue_normally"},
    {"tool": "order_lookup", "order_id": "764654123", "status": "in_transit"}
  ],
  "detected_patterns": [],
  "session_id": "conv-abc-123"
}
```

### Сигнал эскалации

Когда бот определяет, что диалог нужно передать оператору, ответ содержит:

```json
{
  "message": {
    "text": "Передаю ваш вопрос специалисту, он свяжется с вами в ближайшее время.",
    "language": "ru"
  },
  "escalation": {
    "required": true,
    "immediate": true,
    "after_n_turns": null,
    "reason": "legal_threat",
    "operator_context": "Клиент упомянул суд по заказу 764654123. Статус заказа: в пути с 12 марта. 3-е обращение за день. Эмоциональный тон: нарастает. Не продолжать ботом."
  },
  "actions_taken": [
    {"tool": "strategy_suggest", "result": "immediate_supervisor_escalation"}
  ],
  "detected_patterns": ["legal_threat", "repeated_contact", "emotion_escalation"],
  "session_id": "conv-abc-123"
}
```

**Корпоративная система должна проверять `escalation.required`** в каждом ответе:
- `required: true` + `immediate: true` — маршрутизировать на оператора СЕЙЧАС, больше не отправлять сообщения боту
- `required: true` + `after_n_turns: N` — продолжить с ботом ещё N ходов, затем эскалировать если не решено
- `required: false` — продолжать в штатном режиме

### Триггеры эскалации

| Значение `reason` | Описание | Срочность |
|--------------------|----------|-----------|
| `legal_threat` | Клиент упомянул суд, регуляторов, адвокатов | Немедленно |
| `human_request` | Клиент явно попросил оператора | Немедленно |
| `repeated_question` | Бот задал один вопрос 4+ раз | Немедленно |
| `repeated_contact` | 3-е+ обращение клиента за день | Немедленно |
| `no_progress` | Диалог стоит на месте 4+ хода | Немедленно |
| `churn_signal` | Клиент угрожает уйти/отменить | Через 1 ход |
| `emotion_escalation` | Эмоциональная интенсивность клиента растёт | Через 2 хода |

## Интеграция с REST API

### Эндпоинты, которые должна предоставить корпоративная система

#### 1. Поиск заказов

```
GET /api/v1/orders/{order_id}
GET /api/v1/orders?customer_name={name}
GET /api/v1/orders?phone={phone}
```

Ответ:
```json
{
  "order_id": "764654123",
  "status": "in_transit",
  "items": [
    {"sku": "K849", "name": "Название товара", "quantity": 1, "price": 2500.00}
  ],
  "created_at": "2026-03-10T14:30:00Z",
  "delivery_date": "2026-03-16",
  "delivery_time_slot": "14:00-18:00",
  "tracking_number": "TRK-123456",
  "payment_status": "paid",
  "total_amount": 2500.00,
  "delivery_address": "Москва, ул. Ленина, д. 1"
}
```

#### 2. Эскалация / передача оператору

```
POST /api/v1/escalate
```

Запрос:
```json
{
  "session_id": "conv-abc-123",
  "reason": "legal_threat",
  "priority": "critical",
  "operator_context": "Краткое описание ситуации для оператора...",
  "dialogue_history": [
    {"role": "bot", "text": "...", "timestamp": "2026-03-15T12:00:00Z"},
    {"role": "user", "text": "...", "timestamp": "2026-03-15T12:01:00Z"}
  ],
  "detected_patterns": ["legal_threat", "emotion_escalation"],
  "customer_metadata": {
    "total_contacts_today": 3,
    "vip": false
  }
}
```

Ответ:
```json
{
  "status": "transferred",
  "operator_id": "op-42",
  "queue_position": 0,
  "estimated_wait_seconds": 30
}
```

#### 3. События диалога (Webhook / Callback)

Корпоративная система должна уведомлять оркестратор бота о событиях жизненного цикла диалога:

```
POST /api/v1/bot/webhook
```

События:
```json
{"event": "conversation_started", "session_id": "conv-abc-123", "customer_id": "cust-789"}
{"event": "message_received", "session_id": "conv-abc-123", "text": "Где мой заказ?", "role": "user"}
{"event": "operator_joined", "session_id": "conv-abc-123", "operator_id": "op-42"}
{"event": "conversation_ended", "session_id": "conv-abc-123", "resolution": "resolved"}
```

### Эндпоинт, который предоставляет бот

Оркестратор бота предоставляет единый эндпоинт для приёма сообщений пользователя и возврата структурированных ответов:

```
POST /api/v1/bot/message
```

Запрос:
```json
{
  "session_id": "conv-abc-123",
  "text": "Где мой заказ?",
  "customer_metadata": {
    "total_contacts_today": 1,
    "vip": false,
    "customer_name": "Иван Иванов"
  }
}
```

Ответ: структурированный формат, описанный выше.

## Шаги интеграции

### Шаг 1: Демо (текущее состояние)

Бот работает как MCP-сервер, подключённый к Claude Desktop или Claude Code. Все инструменты (`strategy_suggest`, `emotion_analyze` и др.) доступны. Данные заказов вводятся вручную или замоканы.

### Шаг 2: Подключить сервис заказов

Заменить мок `order_lookup` MCP-инструмента на реальный, который вызывает корпоративный REST API. MCP-инструмент внутри делает HTTP-запросы к `GET /api/v1/orders/{id}`.

### Шаг 3: Добавить эндпоинт эскалации

Реализовать оркестратор бота, который:
1. Принимает сообщения пользователя через webhook
2. Вызывает MCP-инструменты (`strategy_suggest` перед каждым ответом)
3. Проверяет `escalation.required` в результате стратегии
4. Если нужна эскалация — вызывает `POST /api/v1/escalate` и останавливает обработку ботом
5. Возвращает структурированный ответ чат-платформе

### Шаг 4: Продакшн-деплой

- Развернуть MCP-сервер как сайдкар или микросервис
- Настроить circuit breaker для вызовов сервиса заказов (уже встроен в `src/nlp/clients.py`)
- Настроить мониторинг: частота эскалаций, статистика обнаруженных паттернов
- Логировать все `detected_patterns` для аналитики

## Процесс эскалации (sequence-диаграмма)

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant C as Чат-платформа
    participant B as Оркестратор бота
    participant M as MCP Server
    participant O as Сервис заказов
    participant E as Сервис эскалации

    U->>C: "Я подам в суд!"
    C->>B: POST /bot/message
    B->>M: strategy_suggest(dialogue_history)
    M-->>B: should_escalate_now: true, reason: legal_threat
    B->>E: POST /escalate (session, context, history)
    E-->>B: transferred, operator_id: op-42
    B-->>C: {message: "Передаю специалисту...", escalation: {required: true}}
    C-->>U: "Передаю специалисту..."
    Note over E: Оператор получает полный контекст
```

## Интеграция сторонних MCP-серверов

В продакшне инструмент `order_lookup` не встроен в этот MCP-сервер — он приходит из **отдельного MCP-сервера**, который подключается к вашей базе данных или системе управления заказами. Несколько MCP-серверов работают вместе, каждый отвечает за свою область.

### Как несколько MCP-серверов работают вместе

```
MCP Host (Claude Desktop / Оркестратор бота)
    ├── emotional-deescalation-mcp    ← этот сервер (стратегия, эмоции)
    ├── order-management-mcp          ← ваш корпоративный MCP (заказы, БД)
    └── other-mcp-servers             ← CRM, платежи и т.д.
```

MCP-хост вызывает инструменты из любого подключённого сервера прозрачно. Бот использует `strategy_suggest` из одного сервера и `order_lookup` из другого в рамках одного диалога.

### Вариант A: MCP-сервер для базы данных

Используйте готовый MCP-сервер для БД, чтобы дать боту прямой SQL-доступ к базе заказов.

**Пример с `@modelcontextprotocol/server-postgres`:**

```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"]
    },
    "orders-db": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres"],
      "env": {
        "POSTGRES_CONNECTION_STRING": "postgresql://user:pass@host:5432/orders_db"
      }
    }
  }
}
```

Это открывает инструменты SQL-запросов. Бот может выполнять запросы вроде:
```sql
SELECT order_id, status, delivery_date FROM orders WHERE order_id = '764654123'
```

**Рекомендуемые MCP-серверы для БД:**
- `@modelcontextprotocol/server-postgres` — PostgreSQL
- `@modelcontextprotocol/server-sqlite` — SQLite
- `mysql-mcp-server` — MySQL

### Вариант B: Собственный MCP-сервер управления заказами

Создайте MCP-сервер, который оборачивает ваш корпоративный REST API и предоставляет доменные инструменты.

**Инструменты для реализации:**

| Инструмент | Описание | Параметры |
|------------|----------|-----------|
| `order_lookup` | Найти заказ по ID, телефону или имени | `order_id`, `phone`, `customer_name` |
| `check_delivery_status` | Отслеживание доставки в реальном времени | `order_id` или `tracking_number` |
| `contact_courier` | Отправить сообщение курьерской службе | `order_id`, `message` |
| `refund_initiate` | Начать процесс возврата | `order_id`, `reason` |
| `open_claim` | Открыть тикет/претензию | `order_id`, `description`, `priority` |
| `escalate_to_human` | Передать оператору | `session_id`, `reason`, `context` |

**Пример скелета MCP-сервера (Python):**

```python
from mcp.server.fastmcp import FastMCP
import httpx

mcp = FastMCP("order-management")
API_BASE = "http://your-corporate-api:8080/api/v1"

@mcp.tool()
async def order_lookup(order_id: str = "", phone: str = "", customer_name: str = "") -> str:
    """Look up order by ID, phone number, or customer name."""
    async with httpx.AsyncClient() as client:
        if order_id:
            resp = await client.get(f"{API_BASE}/orders/{order_id}")
        elif phone:
            resp = await client.get(f"{API_BASE}/orders", params={"phone": phone})
        elif customer_name:
            resp = await client.get(f"{API_BASE}/orders", params={"customer_name": customer_name})
        else:
            return "Please provide order_id, phone, or customer_name"
        resp.raise_for_status()
        return resp.text

@mcp.tool()
async def escalate_to_human(session_id: str, reason: str, context: str) -> str:
    """Transfer conversation to a human operator."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{API_BASE}/escalate", json={
            "session_id": session_id,
            "reason": reason,
            "operator_context": context,
        })
        resp.raise_for_status()
        return resp.text

if __name__ == "__main__":
    mcp.run()
```

**Регистрация в Claude Desktop / Claude Code:**

```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"]
    },
    "order-management": {
      "command": "python",
      "args": ["path/to/order_management_mcp.py"]
    }
  }
}
```

### Вариант C: Гибрид (БД + REST)

Комбинируйте прямой доступ к БД для чтения с REST API для операций записи (возвраты, эскалации):

```json
{
  "mcpServers": {
    "emotional-deescalation": {
      "command": "uvx",
      "args": ["emotional-deescalation-mcp"]
    },
    "orders-readonly": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres"],
      "env": {
        "POSTGRES_CONNECTION_STRING": "postgresql://readonly_user:pass@host:5432/orders_db"
      }
    },
    "order-actions": {
      "command": "python",
      "args": ["path/to/order_actions_mcp.py"]
    }
  }
}
```

### Связь `strategy_suggest` с инструментами заказов

`strategy_suggest` использует `available_actions`, чтобы знать, что бот может делать. Они должны соответствовать инструментам из вашего MCP управления заказами:

```json
{
  "available_actions": ["order_lookup", "check_delivery_status", "contact_courier", "escalate_to_human", "refund_initiate", "open_claim"]
}
```

Когда `strategy_suggest` возвращает `action_sequence`, оркестратор бота сопоставляет действия с соответствующими MCP-инструментами:

```
action_sequence[0].action = "escalate_to_human"  →  вызвать MCP tool escalate_to_human
action_sequence[1].action = "lookup_by_phone"    →  вызвать MCP tool order_lookup(phone=...)
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `ORDER_SERVICE_URL` | Базовый URL REST API заказов | `http://localhost:8080/api/v1` |
| `ESCALATION_SERVICE_URL` | Базовый URL сервиса эскалации | `http://localhost:8080/api/v1` |
| `BOT_API_PORT` | Порт оркестратора бота | `8200` |
| `NLP_SERVICE_URL` | Внешний NLP-сервис (эмбеддинги + эмоции) | `http://localhost:8100` |
| `EMOTION_MCP_MODE` | `host` или `api` | `host` |
