# lite-feed-server

A lightweight event feed server built with FastAPI, featuring REST API endpoints, WebSocket real-time broadcasting, phone call signaling, and SQLite persistence.

## Features

- **POST** an event with title, description, type, optional image (base64 or URL), and optional call actions
- **GET** events with flexible filtering (status, type, exclude types, limit)
- **PATCH** an event to mark it as read/unread
- **WebSocket `/ws`** — real-time event stream for desktop clients, with bidirectional support for routing call actions
- **WebSocket `/ws/calls`** — phone call signaling channel (ringing/ended state + action delivery)
- API key authentication on all endpoints
- Automatic purge of events older than 90 days on startup and on each GET

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/add-event` | Create a new event |
| `GET` | `/get-events` | List events with optional filters |
| `PATCH` | `/update-event/{id}` | Update event status (READ/UNREAD) |
| `WS` | `/ws` | Desktop WebSocket — event stream + action routing |
| `WS` | `/ws/calls` | Phone WebSocket — call signaling |

### Authentication

All endpoints require an `X-API-Key` header (HTTP) or `x_api_key` query parameter (WebSocket). The value can be the raw key or its base64-encoded equivalent.

```
X-API-Key: your-secret-api-key
```

---

### POST /add-event

For regular events, `session_id` and `actions` are omitted. For call notification events, include both:

```json
{
  "title": "John Doe",
  "description": "<html>...",
  "type": "android notification",
  "image": "...",
  "actions": ["answer", "hangup", "mute", "unmute"],
  "session_id": "a3f2c1d4-8b7e-4f3a-9c2d-1e5f6a7b8c9d"
}
```

### GET /get-events — Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | `READ` \| `UNREAD` | Filter by read status |
| `type` | string | Include only this event type |
| `exclude_type` | string (repeatable) | Exclude one or more types |
| `max` | int (1–500) | Max results returned (default: 50) |

**Examples:**
```
GET /get-events?type=bank
GET /get-events?exclude_type=bank&exclude_type=admin
GET /get-events?status=UNREAD&max=10
```

---

## WebSocket /ws — Desktop client

Connect with the API key as a query parameter. Optionally filter by event type:

```
ws://localhost:8000/ws?x_api_key=your-secret-api-key
ws://localhost:8000/ws?x_api_key=your-secret-api-key&type=android+notification
```

**Receiving events:** every event created via `POST /add-event` is broadcast as JSON to all connected clients (filtered by `type` / `exclude_type` if provided).

**Sending call actions:** when a call event is displayed, the desktop can send an action back on the same connection:

```json
{"action": "answer", "session_id": "a3f2c1d4-8b7e-4f3a-9c2d-1e5f6a7b8c9d"}
```

Valid actions: `answer`, `hangup`, `mute`, `unmute`. The server routes the action to the phone connection identified by `session_id`.

---

## WebSocket /ws/calls — Phone client

```
ws://localhost:8000/ws/calls?x_api_key=your-secret-api-key
```

**Phone → Server** (on incoming call):

```json
{"type": "ringing", "number": "+33612345678", "name": "John Doe", "timestamp": 1718000000000, "session_id": "a3f2c1d4-8b7e-4f3a-9c2d-1e5f6a7b8c9d"}
```

The `session_id` must match the one sent in the corresponding `POST /add-event` so the server can link the desktop event to this WebSocket connection.

**Phone → Server** (on call end):

```json
{"type": "ended"}
```

The server broadcasts `{"type": "ended", "session_id": "..."}` to all desktop clients on `/ws` so they can dismiss the call UI.

**Server → Phone** (action commands routed from the desktop):

```json
{"action": "answer"}
{"action": "hangup"}
{"action": "mute"}
{"action": "unmute"}
```

### Call signaling flow

```
Phone ──POST /add-event──────────────────────► Server
       {type, title, actions, session_id}        │ broadcast
                                                  ▼
                                            Desktop /ws ← receives event + action buttons

Phone ──WS /ws/calls ───────────────────────► Server
       {type: "ringing", session_id}             │ registers session_id → ws

Desktop ──WS /ws ───────────────────────────► Server
         {action: "answer", session_id}          │ routes to phone
                                                  ▼
                                            Phone /ws/calls ← {"action": "answer"}
```

---

## Event Schema

```json
{
  "id": "uuid",
  "title": "string",
  "description": "string (optional)",
  "image": "base64 string (optional)",
  "image_url": "string (optional)",
  "type": "string (optional)",
  "status": "UNREAD | READ",
  "pub_date": "2024-01-01 12:00:00",
  "session_id": "string (optional)",
  "actions": ["answer", "hangup", "mute", "unmute"]
}
```

---

## Setup

### Local

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and set API_KEY

# Generate a secure key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Run
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

> **Note:** if upgrading from a version without `session_id`/`actions` support, delete `events.db` before starting — SQLite will recreate the schema automatically.

### Docker

```bash
docker build -t lite-feed-server .
docker run -p 8000:8000 -e API_KEY=your-secret-key lite-feed-server
```

## Tech Stack

- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [SQLModel](https://sqlmodel.tiangolo.com/) — ORM + schema validation (SQLite)
- [Uvicorn](https://www.uvicorn.org/) — ASGI server
- Python 3.11
