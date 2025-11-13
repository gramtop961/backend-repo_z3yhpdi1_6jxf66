import os
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import (
    Tenant, User, Account, Run, RunEvent, SurveyOutcome, Config,
    AccountHealthSnapshot, SiteStatus, Plan, FeatureFlag, RunRecipe, NotificationChannel
)

app = FastAPI(title="GhostForm v2 API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# WebSocket connection manager
# ---------------------------
class ConnectionManager:
    def __init__(self):
        # tenant_id -> list of websockets
        self.active: Dict[str, List[WebSocket]] = {}

    async def connect(self, tenant_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active.setdefault(tenant_id, []).append(websocket)

    def disconnect(self, tenant_id: str, websocket: WebSocket):
        conns = self.active.get(tenant_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns and tenant_id in self.active:
            self.active.pop(tenant_id, None)

    async def broadcast(self, tenant_id: str, message: Dict[str, Any]):
        conns = self.active.get(tenant_id, [])
        for ws in list(conns):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(tenant_id, ws)

manager = ConnectionManager()

# ---------------------------
# Adapters
# ---------------------------
class BaseAdapter:
    site: str = "BASE"

    async def run(self, *, tenant_id: str, account: Dict[str, Any], run_id: str, emit: Callable[[str, str, Optional[Dict[str, Any]]], None]):
        raise NotImplementedError

class FiveSurveysAdapter(BaseAdapter):
    site = "FIVE_SURVEYS"

    async def run(self, *, tenant_id: str, account: Dict[str, Any], run_id: str, emit: Callable[[str, str, Optional[Dict[str, Any]]], None]):
        # Simulated flow with timed events
        try:
            await emit("info", "RUN_STARTED", {"account": account.get("username")})
            await _update_run(run_id, {"status": "LOGIN"})
            await asyncio.sleep(0.5)

            await emit("info", "LOGIN_OK", {"site": self.site})
            await _update_run(run_id, {"status": "CHECK_SURVEYS"})
            await asyncio.sleep(0.5)

            await emit("info", "SURVEYS_FOUND", {"count": 1})
            await _update_run(run_id, {"status": "IN_SURVEY"})
            await asyncio.sleep(1.0)

            payout = 0.75
            duration = 80
            await emit("info", "SURVEY_COMPLETED", {"payout": payout, "duration_sec": duration})
            await _update_run(run_id, {
                "status": "COMPLETED",
                "payout_total": payout,
                "duration_sec_total": duration,
                "revenue_hour": round((payout / max(duration/3600, 1e-6)), 2),
            })

            await emit("info", "RUN_FINISHED", {})
            await _update_run(run_id, {"status": "FINISHED", "updated_at": datetime.now(timezone.utc)})
        except Exception as e:
            await emit("error", "RUN_ERROR", {"error": str(e)})
            await _update_run(run_id, {"status": "ERROR", "error": str(e)})

adapters: Dict[str, BaseAdapter] = {
    "FIVE_SURVEYS": FiveSurveysAdapter(),
}

# ---------------------------
# Helpers
# ---------------------------
async def _emit_and_store(tenant_id: str, run_id: str, level: str, code: str, message: str, data: Optional[Dict[str, Any]] = None):
    event = RunEvent(
        tenant_id=tenant_id,
        run_id=run_id,
        ts=datetime.now(timezone.utc),
        level=level,
        code=code,
        message=message,
        data=data or {},
    )
    create_document("runevent", event)
    await manager.broadcast(tenant_id, {"type": "run_event", "run_id": run_id, "level": level, "code": code, "message": message, "data": data or {}, "ts": event.ts.isoformat()})

async def _update_run(run_id: str, fields: Dict[str, Any]):
    if db is None:
        return
    fields["updated_at"] = fields.get("updated_at", datetime.now(timezone.utc))
    try:
        db["run"].update_one({"_id": ObjectId(run_id)}, {"$set": fields})
    except Exception:
        pass

def _to_object_id(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id format")

# ---------------------------
# API models
# ---------------------------
class CreateAccountRequest(BaseModel):
    tenant_id: str
    site: str
    username: str
    credential_encrypted: str
    proxy_url: Optional[str] = None

class RunNowRequest(BaseModel):
    tenant_id: str
    account_id: str

class RegisterRequest(BaseModel):
    tenant_name: str
    email: str
    password_hash: str

# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def root():
    return {"service": "GhostForm v2 API", "status": "ok"}

@app.get("/test")
def test_database():
    resp = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            resp["database"] = "✅ Available"
            resp["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            resp["database_name"] = db.name
            resp["connection_status"] = "Connected"
            try:
                resp["collections"] = db.list_collection_names()
                resp["database"] = "✅ Connected & Working"
            except Exception as e:
                resp["database"] = f"⚠️ Connected but error: {str(e)[:120]}"
    except Exception as e:
        resp["database"] = f"❌ Error: {str(e)[:120]}"
    return resp

@app.get("/schema")
def get_schema_info():
    collections = [
        "tenant","plan","featureflag","user","account","accounthealthsnapshot",
        "run","runevent","surveyoutcome","config","runrecipe","sitestatus","notificationchannel"
    ]
    return {"collections": collections}

@app.get("/api/accounts")
def list_accounts(tenant_id: Optional[str] = None):
    filter_q = {"tenant_id": tenant_id} if tenant_id else {}
    accounts = get_documents("account", filter_q, limit=None)
    def to_safe(doc: Dict[str, Any]):
        doc = dict(doc)
        if "_id" in doc:
            doc["id"] = str(doc.pop("_id"))
        return doc
    return [to_safe(a) for a in accounts]

@app.post("/api/accounts")
def create_account(req: CreateAccountRequest):
    acc = Account(
        tenant_id=req.tenant_id,
        site=req.site,
        username=req.username,
        credential_encrypted=req.credential_encrypted,
        proxy_url=req.proxy_url,
        status="ACTIVE",
    )
    inserted_id = create_document("account", acc)
    return {"id": inserted_id}

@app.get("/api/runs")
def list_runs(tenant_id: Optional[str] = None):
    filter_q = {"tenant_id": tenant_id} if tenant_id else {}
    runs = get_documents("run", filter_q, limit=100)
    out = []
    for r in runs:
        r = dict(r)
        if "_id" in r:
            r["id"] = str(r.pop("_id"))
        out.append(r)
    return out

@app.get("/api/run-events")
def get_run_events(run_id: str):
    events = get_documents("runevent", {"run_id": run_id}, limit=200)
    out = []
    for e in events:
        e = dict(e)
        if "_id" in e:
            e["id"] = str(e.pop("_id"))
        out.append(e)
    return out

@app.post("/api/run-now")
async def run_now(req: RunNowRequest):
    # Create a run document with INIT state
    run = Run(
        tenant_id=req.tenant_id,
        account_id=req.account_id,
        site="FIVE_SURVEYS",
        status="INIT",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    run_id = create_document("run", run)
    # initial event
    event = RunEvent(
        tenant_id=req.tenant_id,
        run_id=run_id,
        ts=datetime.now(timezone.utc),
        level="info",
        code="RUN_ENQUEUED",
        message="Run enqueued",
    )
    create_document("runevent", event)
    await manager.broadcast(req.tenant_id, {"type": "run_event", "run_id": run_id, "level": "info", "code": "RUN_ENQUEUED", "message": "Run enqueued", "data": {}, "ts": event.ts.isoformat()})

    # load account and select adapter
    account_docs = get_documents("account", {"_id": _to_object_id(req.account_id)}, limit=1)
    if not account_docs:
        raise HTTPException(status_code=404, detail="Account not found")
    account = account_docs[0]
    adapter = adapters.get("FIVE_SURVEYS")

    async def process():
        async def emit(level: str, code: str, data: Optional[Dict[str, Any]] = None):
            await _emit_and_store(req.tenant_id, run_id, level, code, code.replace("_", " "), data)
        await adapter.run(tenant_id=req.tenant_id, account=account, run_id=run_id, emit=emit)

    # fire-and-forget background task
    asyncio.create_task(process())
    return {"run_id": run_id, "status": "processing"}

@app.post("/api/register")
def register(req: RegisterRequest):
    tenant = Tenant(name=req.tenant_name)
    tenant_id = create_document("tenant", tenant)
    user = User(tenant_id=tenant_id, email=req.email, password_hash=req.password_hash, role="owner")
    user_id = create_document("user", user)
    return {"tenant_id": tenant_id, "user_id": user_id}

# ---------------------------
# WebSocket endpoint
# ---------------------------
@app.websocket("/ws/{tenant_id}")
async def websocket_endpoint(websocket: WebSocket, tenant_id: str):
    await manager.connect(tenant_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "tenant_id": tenant_id, "ts": datetime.now(timezone.utc).isoformat()})
        while True:
            # keep connection alive; client may send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(tenant_id, websocket)
    except Exception:
        manager.disconnect(tenant_id, websocket)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
