import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import (
    Tenant, User, Account, Run, RunEvent, SurveyOutcome, Config,
    AccountHealthSnapshot, SiteStatus, Plan, FeatureFlag, RunRecipe, NotificationChannel
)

app = FastAPI(title="GhostForm v2 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CreateAccountRequest(BaseModel):
    tenant_id: str
    site: str
    username: str
    credential_encrypted: str
    proxy_url: Optional[str] = None

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
    # Expose available collection names from our Pydantic models
    collections = [
        "tenant","plan","featureflag","user","account","accounthealthsnapshot",
        "run","runevent","surveyoutcome","config","runrecipe","sitestatus","notificationchannel"
    ]
    return {"collections": collections}

@app.get("/api/accounts")
def list_accounts(tenant_id: Optional[str] = None):
    filter_q = {"tenant_id": tenant_id} if tenant_id else {}
    accounts = get_documents("account", filter_q, limit=None)
    # convert ObjectId to str where needed
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

class RunNowRequest(BaseModel):
    tenant_id: str
    account_id: str

@app.post("/api/run-now")
def run_now(req: RunNowRequest):
    # For v1 skeleton, just enqueue a Run document with INIT state
    run = Run(
        tenant_id=req.tenant_id,
        account_id=req.account_id,
        site="FIVE_SURVEYS",
        status="INIT",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    run_id = create_document("run", run)
    event = RunEvent(
        tenant_id=req.tenant_id,
        run_id=run_id,
        ts=datetime.now(timezone.utc),
        level="info",
        code="RUN_ENQUEUED",
        message="Run enqueued (worker not yet implemented)",
    )
    create_document("runevent", event)
    return {"run_id": run_id, "status": "enqueued"}

# Minimal auth placeholder (email/password hashing to be added in future steps)
class RegisterRequest(BaseModel):
    tenant_name: str
    email: str
    password_hash: str

@app.post("/api/register")
def register(req: RegisterRequest):
    tenant = Tenant(name=req.tenant_name)
    tenant_id = create_document("tenant", tenant)
    user = User(tenant_id=tenant_id, email=req.email, password_hash=req.password_hash, role="owner")
    user_id = create_document("user", user)
    return {"tenant_id": tenant_id, "user_id": user_id}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
