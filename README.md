# lite-feed-server

A lightweight event feed server built with FastAPI, featuring REST API endpoints, WebSocket real-time broadcasting, and SQLite persistence.

## Features

- **POST** an event with title, description, type, and optional image (base64 or URL)
- **GET** events with flexible filtering (status, type, exclude types, limit)
- **PATCH** an event to mark it as read/unread
- **WebSocket** endpoint for real-time push of new events to connected clients
- API key authentication on all endpoints
- Automatic purge of events older than 90 days on startup and on each GET

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/add-event` | Create a new event |
| `GET` | `/get-events` | List events with optional filters |
| `PATCH` | `/update-event/{id}` | Update event status (READ/UNREAD) |
| `WS` | `/ws` | WebSocket — real-time event stream |

### Authentication

All endpoints require an `X-API-Key` header. The value can be the raw key or its base64-encoded equivalent.

```
X-API-Key: your-secret-api-key
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

### WebSocket /ws

Connect with the API key as a query parameter:

```
ws://localhost:8000/ws?x_api_key=your-secret-api-key
```

Each new event created via `POST /add-event` is immediately broadcast as JSON to all connected clients.

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
  "pub_date": "2024-01-01 12:00:00"
}
```

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
