"""
GET /audit/{request_id} — Full agent conversation history for compliance.

Returns all per-agent-turn entries from authagent-audit table plus
a CloudWatch Transaction Search URL for the OTEL trace.
"""

import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, HTTPException
from opentelemetry import trace

from authagent.models.response import AuditTrail, AgentTurn

router = APIRouter()
tracer = trace.get_tracer("authagent.api.audit")

AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "authagent-audit")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)


def _decimal_to_native(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_native(i) for i in obj]
    return obj


def _cloudwatch_url(trace_id: str, region: str) -> str:
    """Build a CloudWatch Transaction Search URL for the given trace ID."""
    if not trace_id or trace_id == "0x0":
        return ""
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#xray:traces/{trace_id}"
    )


@router.get("/audit/{request_id}", response_model=AuditTrail)
async def get_audit_trail(request_id: str):
    """Return the complete agent conversation history and OTEL trace link."""
    with tracer.start_as_current_span("get_audit") as span:
        span.set_attribute("request.id", request_id)

        table = _dynamodb.Table(AUDIT_TABLE)
        response = table.query(
            KeyConditionExpression=Key("request_id").eq(request_id)
        )
        items = _decimal_to_native(response.get("Items", []))

        if not items:
            raise HTTPException(status_code=404, detail=f"Audit trail for {request_id} not found")

        turns = []
        trace_id = ""
        for item in sorted(items, key=lambda x: x.get("timestamp", "")):
            trace_id = item.get("trace_id", trace_id)
            turns.append(
                AgentTurn(
                    agent_name=item.get("agent_name", ""),
                    action=item.get("action", ""),
                    reasoning=item.get("reasoning", ""),
                    guideline_ids=item.get("guideline_ids", []),
                    duration_ms=item.get("duration_ms", 0),
                    timestamp=item.get("timestamp", ""),
                    trace_id=item.get("trace_id", ""),
                )
            )

        return AuditTrail(
            request_id=request_id,
            turns=turns,
            trace_id=trace_id,
            cloudwatch_url=_cloudwatch_url(trace_id, AWS_REGION),
        )
