from base64 import b64decode
from contextlib import asynccontextmanager
import json
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Header, Depends
from fastapi.encoders import jsonable_encoder
from sqlmodel import Field, Session, SQLModel, create_engine, select
from sqlalchemy import Column, JSON as SAJson, text
from datetime import datetime, timedelta
from typing import Annotated, List, Optional
from enum import Enum
import uuid
import os
from dotenv import load_dotenv

load_dotenv()

# --- Configuration & Models ---

API_KEY = os.getenv("API_KEY", "xx")
if not API_KEY:
    raise ValueError("API_KEY must be set in environment variables")

VALID_ACTIONS = {"answer", "hangup", "mute", "unmute"}


def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")):
    decoded_x_api_key = ""
    try:
        decoded_x_api_key = b64decode(x_api_key).decode()
    except Exception:
        pass

    if x_api_key != API_KEY and decoded_x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key


def verify_ws_api_key(x_api_key: str) -> bool:
    decoded = ""
    try:
        decoded = b64decode(x_api_key).decode()
    except Exception:
        pass
    return x_api_key == API_KEY or decoded == API_KEY


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[WebSocket, dict] = {}

    async def connect(self, websocket: WebSocket, type: Optional[str], exclude_type: List[str]):
        await websocket.accept()
        self.active_connections[websocket] = {"type": type, "exclude_type": exclude_type}

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(websocket, None)

    async def broadcast(self, message: dict):
        event_type = message.get("type")
        for connection, filters in self.active_connections.items():
            if filters["type"] and event_type != filters["type"]:
                continue
            if filters["exclude_type"] and event_type in filters["exclude_type"]:
                continue
            await connection.send_json(message)


class CallManager:
    def __init__(self):
        self.calls: dict[str, WebSocket] = {}

    def register(self, session_id: str, websocket: WebSocket):
        self.calls[session_id] = websocket

    def unregister(self, session_id: str):
        self.calls.pop(session_id, None)

    async def send_action(self, session_id: str, action: str) -> bool:
        ws = self.calls.get(session_id)
        if not ws:
            return False
        await ws.send_json({"action": action})
        return True


manager = ConnectionManager()
call_manager = CallManager()


class StatusEnum(str, Enum):
    READ = "READ"
    UNREAD = "UNREAD"


# database model
class Event(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    title: str
    description: Optional[str] = None
    image: Optional[str] = None  # Base64 string
    image_url: Optional[str] = None
    type: Optional[str] = None
    status: StatusEnum = Field(default=StatusEnum.UNREAD)
    pub_date: datetime = Field(default_factory=datetime.now)
    session_id: Optional[str] = None
    actions: Optional[List[str]] = Field(default=None, sa_column=Column(SAJson))


# model for creation (the user does not send the ID nor the date)
class EventCreate(SQLModel):
    title: str
    description: Optional[str] = None
    image: Annotated[Optional[str], Field(default=None, description="Image encodée en base64 (ex: `data:image/png;base64,iVBORw0KGgo...`)")]
    image_url: Optional[str] = None
    type: Optional[str] = None
    session_id: Optional[str] = None
    actions: Optional[List[str]] = None


# model for update (only status can be updated)
class EventUpdate(SQLModel):
    status: StatusEnum


# Setup DB (SQLite)
sqlite_file_name = os.getenv("DB_PATH", "events.db")
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def migrate_db():
    migrations = [
        ("session_id", "ALTER TABLE event ADD COLUMN session_id VARCHAR"),
        ("actions",    "ALTER TABLE event ADD COLUMN actions JSON"),
    ]
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(event)"))}
        for column, statement in migrations:
            if column not in existing:
                conn.execute(text(statement))
        conn.commit()


# --- Application ---

def purge_old_events():
    cutoff = datetime.now() - timedelta(days=90)
    with Session(engine) as session:
        old_events = session.exec(select(Event).where(Event.pub_date < cutoff)).all()
        for event in old_events:
            session.delete(event)
        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    migrate_db()
    purge_old_events()
    yield


app = FastAPI(
    title="Event Tracker Light",
    description="API légère de suivi d'événements avec WebSocket et filtrage.",
    version="1.0.0",
    lifespan=lifespan,
)


# 1. Endpoint: add-event
@app.post("/add-event", response_model=Event)
async def add_event(event_data: EventCreate, _: str = Depends(verify_api_key)):
    with Session(engine) as session:
        event = Event.model_validate(event_data, from_attributes=True)
        session.add(event)
        session.commit()
        session.refresh(event)

        event_json = jsonable_encoder(event)
        await manager.broadcast(event_json)

        return event


# 2. Endpoint: get-events
@app.get(
    "/get-events",
    summary="Récupérer les événements",
    description=(
        "Retourne la liste des événements avec filtrage optionnel.\n\n"
        "**Exemples :**\n"
        "- `?type=banque` → uniquement les événements de type *banque*\n"
        "- `?exclude_type=banque` → tout sauf *banque*\n"
        "- `?exclude_type=banque&exclude_type=admin` → tout sauf *banque* et *admin*\n"
        "- `?status=UNREAD&exclude_type=banque` → non-lus, hors *banque*"
    ),
    tags=["Événements"],
)
def get_events(
    status: Annotated[Optional[StatusEnum], Query(description="Filter by status (`READ` or `UNREAD`)")] = None,
    type: Annotated[Optional[str], Query(description="Only include this type of event")] = None,
    exclude_type: Annotated[List[str], Query(description="Exclude one or more types (repeat the parameter for multiple values)")] = [],
    max: Annotated[int, Query(ge=1, le=500, description="Maximum number of events to return")] = 50,
    _: Annotated[str, Depends(verify_api_key)] = None,
):
    purge_old_events()
    with Session(engine) as session:
        statement = select(Event)

        if status:
            statement = statement.where(Event.status == status)

        if type:
            statement = statement.where(Event.type == type)

        if exclude_type:
            statement = statement.where(Event.type.not_in(exclude_type))

        statement = statement.order_by(Event.pub_date.desc()).limit(max)

        results = session.exec(statement).all()

        formatted_results = []
        for event in results:
            event_dict = event.model_dump()
            event_dict['id'] = str(event.id)
            event_dict['pub_date'] = event.pub_date.strftime("%Y-%m-%d %H:%M:%S")
            formatted_results.append(event_dict)

        return formatted_results


# 3. Endpoint: update-event
@app.patch("/update-event/{event_id}", response_model=Event)
def update_event(event_id: uuid.UUID, update_data: EventUpdate, _: str = Depends(verify_api_key)):
    with Session(engine) as session:
        event = session.get(Event, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Événement non trouvé")

        event.status = update_data.status
        session.add(event)
        session.commit()
        session.refresh(event)

        return event


# 4. WebSocket: desktop notifications feed
@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    x_api_key: str = Query(...),
    type: Optional[str] = Query(default=None),
    exclude_type: List[str] = Query(default=[]),
):
    if not verify_ws_api_key(x_api_key):
        await websocket.close(code=1008, reason="Invalid API Key")
        return

    await manager.connect(websocket, type, exclude_type)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                data = json.loads(text)
                action = data.get("action")
                session_id = data.get("session_id")
                if action in VALID_ACTIONS and session_id:
                    await call_manager.send_action(session_id, action)
            except (json.JSONDecodeError, TypeError):
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# 5. WebSocket: phone call signaling
@app.websocket("/ws/calls")
async def websocket_calls(
    websocket: WebSocket,
    x_api_key: str = Query(...),
):
    if not verify_ws_api_key(x_api_key):
        await websocket.close(code=1008, reason="Invalid API Key")
        return

    await websocket.accept()
    session_id: Optional[str] = None
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ringing" and (session_id := data.get("session_id")):
                call_manager.register(session_id, websocket)
            elif msg_type == "ended" and session_id:
                call_manager.unregister(session_id)
                await manager.broadcast({"type": "ended", "session_id": session_id})
                session_id = None
    except WebSocketDisconnect:
        if session_id:
            call_manager.unregister(session_id)
