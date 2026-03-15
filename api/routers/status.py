"""
GET /status/{request_id} — Poll processing status.
"""

import os

import boto3
from fastapi import APIRouter, HTTPException
from opentelemetry import trace

from authagent.models.response import AuthStatus

router = APIRouter()
tracer = trace.get_tracer("authagent.api.status")

DECISIONS_TABLE = os.environ.get("DECISIONS_TABLE", "authagent-decisions")
_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


@router.get("/status/{request_id}", response_model=AuthStatus)
async def get_status(request_id: str):
    """Return the current processing status of a prior authorization request."""
    with tracer.start_as_current_span("get_status") as span:
        span.set_attribute("request.id", request_id)

        table = _dynamodb.Table(DECISIONS_TABLE)
        response = table.get_item(Key={"request_id": request_id, "sk": "METADATA"})
        item = response.get("Item")

        if not item:
            raise HTTPException(status_code=404, detail=f"Request {request_id} not found")

        return AuthStatus(
            request_id=request_id,
            status=item.get("status", "UNKNOWN"),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", item.get("created_at", "")),
        )
