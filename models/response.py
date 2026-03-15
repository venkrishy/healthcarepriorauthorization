from typing import Literal, Optional
from pydantic import BaseModel, Field


class GuidelineRef(BaseModel):
    guideline_id: str
    title: str
    policy_type: str  # "LCD" | "NCD" | "CPT" | "ICD10"
    relevance_score: float
    excerpt: str


class AgentTurn(BaseModel):
    agent_name: str
    action: str
    reasoning: str
    guideline_ids: list[str] = Field(default_factory=list)
    duration_ms: int
    timestamp: str
    trace_id: str


class AuthDecision(BaseModel):
    request_id: str
    decision: Literal["APPROVED", "PENDED", "DENIED"]
    necessity_score: int = Field(..., ge=0, le=100)
    guidelines_cited: list[GuidelineRef] = Field(default_factory=list)
    rationale: str
    missing_information: list[str] = Field(default_factory=list)
    denial_reason: Optional[str] = None
    audit_trail: list[AgentTurn] = Field(default_factory=list)
    trace_id: str
    created_at: str
    duration_ms: int


class AuthStatus(BaseModel):
    request_id: str
    status: Literal["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
    created_at: str
    updated_at: str


class AuditTrail(BaseModel):
    request_id: str
    turns: list[AgentTurn]
    trace_id: str
    cloudwatch_url: Optional[str] = None
