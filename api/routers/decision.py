"""
GET /decision/{request_id} — Retrieve the final authorization decision.
"""

import os
from decimal import Decimal

import boto3
from fastapi import APIRouter, HTTPException
from opentelemetry import trace

from authagent.models.response import AuthDecision, AgentTurn, GuidelineRef

router = APIRouter()
tracer = trace.get_tracer("authagent.api.decision")

DECISIONS_TABLE = os.environ.get("DECISIONS_TABLE", "authagent-decisions")
_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _decimal_to_float(obj):
    """Recursively convert DynamoDB Decimal types to float."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_float(i) for i in obj]
    return obj


@router.get("/decision/{request_id}", response_model=AuthDecision)
async def get_decision(request_id: str):
    """Return the complete authorization decision with guidelines cited and rationale."""
    with tracer.start_as_current_span("get_decision") as span:
        span.set_attribute("request.id", request_id)

        table = _dynamodb.Table(DECISIONS_TABLE)
        response = table.get_item(Key={"request_id": request_id, "sk": "METADATA"})
        item = response.get("Item")

        if not item:
            raise HTTPException(status_code=404, detail=f"Request {request_id} not found")

        if item.get("status") not in ("COMPLETED",):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "decision_not_ready",
                    "status": item.get("status", "UNKNOWN"),
                    "message": "Decision is still being processed. Poll /status/{request_id}.",
                },
            )

        item = _decimal_to_float(item)

        guidelines = [
            GuidelineRef(**g) if isinstance(g, dict) else GuidelineRef(
                guideline_id=str(g),
                title="",
                policy_type="LCD",
                relevance_score=0.0,
                excerpt="",
            )
            for g in item.get("guidelines_cited", [])
        ]

        turns = [
            AgentTurn(**t) if isinstance(t, dict) else AgentTurn(
                agent_name=str(t),
                action="",
                reasoning="",
                guideline_ids=[],
                duration_ms=0,
                timestamp="",
                trace_id="",
            )
            for t in item.get("audit_trail", [])
        ]

        return AuthDecision(
            request_id=request_id,
            decision=item["decision"],
            necessity_score=int(item.get("necessity_score", 0)),
            guidelines_cited=guidelines,
            rationale=item.get("rationale", ""),
            missing_information=item.get("missing_information", []),
            denial_reason=item.get("denial_reason"),
            audit_trail=turns,
            trace_id=item.get("trace_id", ""),
            created_at=item.get("created_at", ""),
            duration_ms=int(item.get("duration_ms", 0)),
        )
