from __future__ import annotations
from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime

# Multi-tenant core
class Tenant(BaseModel):
    name: str
    plan_id: Optional[str] = None
    features: List[str] = Field(default_factory=list)

class Plan(BaseModel):
    name: str
    price_monthly: float = 0.0
    limits: Dict[str, int] = Field(default_factory=dict)

class FeatureFlag(BaseModel):
    key: str
    enabled: bool = True
    description: Optional[str] = None

class User(BaseModel):
    tenant_id: str
    email: EmailStr
    password_hash: str
    role: Literal["owner", "admin", "member"] = "owner"
    created_at: Optional[datetime] = None

# Accounts and health
class Account(BaseModel):
    tenant_id: str
    site: str = Field(..., description="Site identifier, e.g., FIVE_SURVEYS")
    username: str
    credential_encrypted: str = Field(..., description="AES-256-GCM encrypted secret blob")
    proxy_url: Optional[str] = None
    fingerprint: Optional[Dict[str, Any]] = None
    behavior_profile: Optional[Dict[str, Any]] = None
    status: Literal["ACTIVE", "PAUSED", "ON_HOLD"] = "ACTIVE"
    last_run_at: Optional[datetime] = None
    revenue_hour: float = 0.0
    health_score: float = 100.0

class AccountHealthSnapshot(BaseModel):
    tenant_id: str
    account_id: str
    health_score: float
    disqual_rate: float
    error_rate: float
    friction_signals: int = 0
    created_at: Optional[datetime] = None

# Runs and outcomes
class Run(BaseModel):
    tenant_id: str
    account_id: str
    site: str
    status: Literal[
        "INIT","LOGIN","CHECK_SURVEYS","SELECT_SURVEY","START_SURVEY","IN_SURVEY",
        "COMPLETED","DISQUALIFIED","NO_SURVEYS","ERROR","FINISHED"
    ] = "INIT"
    payout_total: float = 0.0
    duration_sec_total: int = 0
    ev_score_avg: float = 0.0
    revenue_hour: float = 0.0
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class RunEvent(BaseModel):
    tenant_id: str
    run_id: str
    ts: Optional[datetime] = None
    level: Literal["info","warn","error"] = "info"
    code: str
    message: str
    data: Optional[Dict[str, Any]] = None

class SurveyOutcome(BaseModel):
    tenant_id: str
    run_id: str
    account_id: str
    site: str
    survey_id: str
    status: Literal["COMPLETED","DISQUALIFIED","ERROR"]
    payout: float = 0.0
    duration_sec: int = 0
    ev_score: float = 0.0
    tags: List[str] = Field(default_factory=list)

# Config and recipes
class Config(BaseModel):
    tenant_id: str
    key: str
    value: Any

class RunRecipe(BaseModel):
    tenant_id: str
    name: str
    sites: List[str]
    account_ids: List[str] = Field(default_factory=list)
    strategy: Literal["FIRST_IN_LIST","EV_MAXIMIZER","SUCCESS_RATE_PRIORITY","HYBRID"] = "EV_MAXIMIZER"
    schedule_cron: Optional[str] = None

# Site status and notifications
class SiteStatus(BaseModel):
    site: str
    status: Literal["HEALTHY","DEGRADED","BROKEN"] = "HEALTHY"
    dom_signature_hash: Optional[str] = None
    last_checked_at: Optional[datetime] = None

class NotificationChannel(BaseModel):
    tenant_id: str
    kind: Literal["telegram","email","webhook"]
    config: Dict[str, Any]

# Helper model for exposing schemas to UI tools
class SchemaInfo(BaseModel):
    collections: List[str]
