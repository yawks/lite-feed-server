from base64 import b64decode
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Header, Depends
from fastapi.encoders import jsonable_encoder
from sqlmodel import Field, Session, SQLModel, create_engine, select
from datetime import datetime, timedelta
from typing import Annotated, List, Optional
from enum import Enum
import uuid
import os
from dotenv import load_dotenv

load_dotenv()

# --- Configuration & Models ---

API_KEY = os.getenv("API_KEY","xx")
if not API_KEY:
    raise ValueError("API_KEY must be set in environment variables")

def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")):
    decoded_x_api_key = ""
    try:
        decoded_x_api_key = b64decode(x_api_key).decode()
    except Exception:
        pass

    if x_api_key != API_KEY and decoded_x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key

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

manager = ConnectionManager()

class StatusEnum(str, Enum):
    READ = "READ"
    UNREAD = "UNREAD"

# database model
class Event(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    title: str
    description: Optional[str] = None
    image: Optional[str] = None # Base64 string
    image_url: Optional[str] = None
    type: Optional[str] = None
    status: StatusEnum = Field(default=StatusEnum.UNREAD)
    pub_date: datetime = Field(default_factory=datetime.now)


# model  for creation (the user does not send the ID nor the date)
class EventCreate(SQLModel):
    title: str
    description: Optional[str] = None
    image: Annotated[Optional[str], Field(default=None, description="Image encodée en base64 (ex: `data:image/png;base64,iVBORw0KGgo...`)")]
    image_url: Optional[str] = None
    type: Optional[str] = None

# model for update (only status can be updated)
class EventUpdate(SQLModel):
    status: StatusEnum

# Setup DB (SQLite)
sqlite_file_name = "events.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

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

        # --- WEBSOCKET PART ---
        # convert object to dict and format date as string
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

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    x_api_key: str = Query(...),
    type: Optional[str] = Query(default=None),
    exclude_type: List[str] = Query(default=[]),
):
    if x_api_key != API_KEY:
        await websocket.close(code=1008, reason="Invalid API Key")
        return

    await manager.connect(websocket, type, exclude_type)
    try:
        while True:
            # keep the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

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