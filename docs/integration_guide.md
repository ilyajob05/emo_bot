[Русская версия](integration_guide.ru.md)

# Integration Guide: Connecting to a Corporate System

This document describes how to integrate the MCP-based support bot into a production environment with a corporate REST API.

## Current Mode

The bot currently operates in **demo mode** — it communicates directly with a user via an MCP host (Claude Desktop, Claude Code, etc.). In production, the bot sits between the user and a corporate backend, exchanging structured messages via REST API.

## Architecture Overview

```
Demo mode:
  User ←→ MCP Host (Claude) ←→ MCP Server (strategy_suggest, emotion tools)

Production mode:
  User ←→ Chat Platform (web/mobile/telegram)
           ↕
         Corporate API Gateway
           ↕
         Bot Orchestrator
           ├── MCP Server (strategy_suggest, emotion tools)
           ├── Order Service REST API (order_lookup, delivery status)
           └── Escalation Service (transfer to human operator)
```

## Bot Response Format

In production, the bot must return a **structured response** (not just plain text) so the corporate system can parse it and act accordingly.

### Response Schema

```json
{
  "message": {
    "text": "Your order 764654123 is on the way. Expected delivery: March 16, 2:00–6:00 PM.",
    "language": "en"
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

### Escalation Signal

When the bot determines that the conversation must be transferred to a human operator, the response contains:

```json
{
  "message": {
    "text": "I'm transferring you to a specialist who can help resolve this.",
    "language": "en"
  },
  "escalation": {
    "required": true,
    "immediate": true,
    "after_n_turns": null,
    "reason": "legal_threat",
    "operator_context": "Customer mentioned lawsuit regarding order 764654123. Order status: in_transit since March 12. Customer's 3rd contact today. Emotional tone: escalating. Do not continue with bot."
  },
  "actions_taken": [
    {"tool": "strategy_suggest", "result": "immediate_supervisor_escalation"}
  ],
  "detected_patterns": ["legal_threat", "repeated_contact", "emotion_escalation"],
  "session_id": "conv-abc-123"
}
```

**The corporate system should check `escalation.required`** on every response:
- `required: true` + `immediate: true` — route to operator NOW, do not send any more messages to the bot
- `required: true` + `after_n_turns: N` — continue with bot for N more turns, then escalate if unresolved
- `required: false` — continue normally

### Escalation Triggers

| `reason` value | Description | Urgency |
|----------------|-------------|---------|
| `legal_threat` | Customer mentioned lawsuits, regulators, lawyers | Immediate |
| `human_request` | Customer explicitly asked for a human agent | Immediate |
| `repeated_question` | Bot asked the same question 4+ times | Immediate |
| `repeated_contact` | Customer's 3rd+ contact today | Immediate |
| `no_progress` | Dialogue stuck for 4+ turns | Immediate |
| `churn_signal` | Customer threatens to leave/cancel | After 1 turn |
| `emotion_escalation` | Customer's emotional intensity rising | After 2 turns |

## REST API Integration

### Endpoints the Bot Orchestrator Needs

The corporate system must expose these REST endpoints for the bot to function in production:

#### 1. Order Lookup

```
GET /api/v1/orders/{order_id}
GET /api/v1/orders?customer_name={name}
GET /api/v1/orders?phone={phone}
```

Response:
```json
{
  "order_id": "764654123",
  "status": "in_transit",
  "items": [
    {"sku": "K849", "name": "Product Name", "quantity": 1, "price": 2500.00}
  ],
  "created_at": "2026-03-10T14:30:00Z",
  "delivery_date": "2026-03-16",
  "delivery_time_slot": "14:00-18:00",
  "tracking_number": "TRK-123456",
  "payment_status": "paid",
  "total_amount": 2500.00,
  "delivery_address": "Moscow, Lenina st. 1"
}
```

#### 2. Escalation / Transfer to Operator

```
POST /api/v1/escalate
```

Request:
```json
{
  "session_id": "conv-abc-123",
  "reason": "legal_threat",
  "priority": "critical",
  "operator_context": "Summary of the situation for the operator...",
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

Response:
```json
{
  "status": "transferred",
  "operator_id": "op-42",
  "queue_position": 0,
  "estimated_wait_seconds": 30
}
```

#### 3. Conversation Events (Webhook / Callback)

The corporate system should notify the bot orchestrator about conversation lifecycle events:

```
POST /api/v1/bot/webhook
```

Events:
```json
{"event": "conversation_started", "session_id": "conv-abc-123", "customer_id": "cust-789"}
{"event": "message_received", "session_id": "conv-abc-123", "text": "Where is my order?", "role": "user"}
{"event": "operator_joined", "session_id": "conv-abc-123", "operator_id": "op-42"}
{"event": "conversation_ended", "session_id": "conv-abc-123", "resolution": "resolved"}
```

### Endpoints the Bot Exposes

The bot orchestrator exposes a single endpoint for receiving user messages and returning structured responses:

```
POST /api/v1/bot/message
```

Request:
```json
{
  "session_id": "conv-abc-123",
  "text": "Where is my order?",
  "customer_metadata": {
    "total_contacts_today": 1,
    "vip": false,
    "customer_name": "Ivan Ivanov"
  }
}
```

Response: the structured response format described above.

## Integration Steps

### Step 1: Demo (Current State)

The bot runs as an MCP server, connected to Claude Desktop or Claude Code. All tools (`strategy_suggest`, `emotion_analyze`, etc.) are available. Order data is mocked or entered manually.

### Step 2: Connect Order Service

Replace the mock `order_lookup` MCP tool with one that calls the real corporate REST API. The MCP tool internally makes HTTP requests to `GET /api/v1/orders/{id}`.

### Step 3: Add Escalation Endpoint

Implement the bot orchestrator that:
1. Receives user messages via webhook
2. Calls MCP tools (`strategy_suggest` before every reply)
3. Checks `escalation.required` in strategy result
4. If escalation needed — calls `POST /api/v1/escalate` and stops bot processing
5. Returns structured response to the chat platform

### Step 4: Production Deployment

- Deploy MCP server as a sidecar or microservice
- Configure circuit breaker for order service calls (already built into `src/nlp/clients.py`)
- Set up monitoring for escalation rates, pattern detection stats
- Log all `detected_patterns` for analytics

## Escalation Flow (Sequence Diagram)

```mermaid
sequenceDiagram
    participant U as User
    participant C as Chat Platform
    participant B as Bot Orchestrator
    participant M as MCP Server
    participant O as Order Service
    participant E as Escalation Service

    U->>C: "I'll sue you!"
    C->>B: POST /bot/message
    B->>M: strategy_suggest(dialogue_history)
    M-->>B: should_escalate_now: true, reason: legal_threat
    B->>E: POST /escalate (session, context, history)
    E-->>B: transferred, operator_id: op-42
    B-->>C: {message: "Transferring to specialist...", escalation: {required: true}}
    C-->>U: "Transferring to specialist..."
    Note over E: Operator receives full context
```

## Integrating Third-Party MCP Servers

In production, the `order_lookup` tool is not built into this MCP server — it comes from a **separate MCP server** that connects to your database or order management system. Multiple MCP servers work together, each responsible for its own domain.

### How Multiple MCP Servers Work Together

```
MCP Host (Claude Desktop / Bot Orchestrator)
    ├── emotional-deescalation-mcp    ← this server (strategy, emotions)
    ├── order-management-mcp          ← your corporate MCP (orders, DB)
    └── other-mcp-servers             ← CRM, payments, etc.
```

The MCP host calls tools from any connected server transparently. The bot uses `strategy_suggest` from one server and `order_lookup` from another in the same conversation.

### Option A: Database MCP Server

Use an existing database MCP server to give the bot direct SQL/query access to your order database.

**Example with `@modelcontextprotocol/server-postgres`:**

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

This exposes SQL query tools. The bot can run queries like:
```sql
SELECT order_id, status, delivery_date FROM orders WHERE order_id = '764654123'
```

**Recommended database MCP servers:**
- `@modelcontextprotocol/server-postgres` — PostgreSQL
- `@modelcontextprotocol/server-sqlite` — SQLite
- `mysql-mcp-server` — MySQL

### Option B: Custom Order Management MCP Server

Build a custom MCP server that wraps your corporate REST API and exposes domain-specific tools.

**Tools to implement:**

| Tool | Description | Parameters |
|------|-------------|------------|
| `order_lookup` | Find order by ID, phone, or customer name | `order_id`, `phone`, `customer_name` |
| `check_delivery_status` | Real-time delivery tracking | `order_id` or `tracking_number` |
| `contact_courier` | Send message to courier service | `order_id`, `message` |
| `refund_initiate` | Start refund process | `order_id`, `reason` |
| `open_claim` | Open a support ticket/claim | `order_id`, `description`, `priority` |
| `escalate_to_human` | Transfer to human operator | `session_id`, `reason`, `context` |

**Example MCP server skeleton (Python):**

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

**Register in Claude Desktop / Claude Code:**

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

### Option C: Hybrid (DB + REST)

Combine direct database access for read-only queries with REST API calls for write operations (refunds, escalations):

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

### Connecting `strategy_suggest` with Order Tools

The `strategy_suggest` tool uses `available_actions` to know what the bot can do. These should match the tools from your order management MCP:

```json
{
  "available_actions": ["order_lookup", "check_delivery_status", "contact_courier", "escalate_to_human", "refund_initiate", "open_claim"]
}
```

When `strategy_suggest` returns an `action_sequence`, the bot orchestrator maps actions to the corresponding MCP tools:

```
action_sequence[0].action = "escalate_to_human"  →  call escalate_to_human MCP tool
action_sequence[1].action = "lookup_by_phone"    →  call order_lookup(phone=...) MCP tool
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ORDER_SERVICE_URL` | Base URL of the order REST API | `http://localhost:8080/api/v1` |
| `ESCALATION_SERVICE_URL` | Base URL of the escalation service | `http://localhost:8080/api/v1` |
| `BOT_API_PORT` | Port the bot orchestrator listens on | `8200` |
| `NLP_SERVICE_URL` | External NLP service (embeddings + emotion) | `http://localhost:8100` |
| `EMOTION_MCP_MODE` | `host` or `api` | `host` |
