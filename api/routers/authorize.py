"""
POST /authorize — Submit a prior authorization request.

Validates the request, checks rate limits, writes PENDING status to DynamoDB,
then invokes the AgentCore runtime (or runs graph inline for local dev).
Returns request_id immediately; client polls /status/{request_id}.
"""

import json
import os
import time
import uuid

import boto3
from fastapi import APIRouter, HTTPException, Request, status
from opentelemetry import trace

from authagent.models.request import PriorAuthRequest
from authagent.models.response import AuthStatus
from authagent.tools.rate_limiter import RateLimitExceeded, check_and_increment
from authagent.tools.write_decision import update_status

router = APIRouter()
tracer = trace.get_tracer("authagent.api.authorize")

DECISIONS_TABLE = os.environ.get("DECISIONS_TABLE", "authagent-decisions")
AGENTCORE_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
RUN_INLINE = os.environ.get("RUN_INLINE", "false").lower() == "true"
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
_bedrock_agentcore = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))
_lambda_client = boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "us-east-1"))


@router.post("/authorize", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def submit_authorization(request_body: PriorAuthRequest, http_request: Request):
    """
    Submit a prior authorization request for processing.

    Rate limits enforced: 100/day, 3100/month (DynamoDB atomic counters).
    Returns 202 Accepted with request_id; poll /status/{request_id} for result.
    """
    with tracer.start_as_current_span("authorize_submit") as span:
        request_id = str(uuid.uuid4())
        span.set_attribute("request.id", request_id)
        span.set_attribute("request.procedure_code", request_body.procedure_code)

        # Layer 2: application-level rate limiting (Layer 1 = API Gateway usage plan)
        try:
            usage = check_and_increment()
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "period": exc.period,
                    "limit": exc.limit,
                    "message": f"{exc.period.capitalize()} request limit of {exc.limit} reached. "
                               f"Try again in {exc.retry_after_seconds} seconds.",
                },
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc

        # Write PENDING status to DynamoDB immediately
        import datetime
        now = datetime.datetime.utcnow().isoformat() + "Z"
        decisions_table = _dynamodb.Table(DECISIONS_TABLE)
        decisions_table.put_item(
            Item={
                "request_id": request_id,
                "sk": "METADATA",
                "status": "PROCESSING",
                "created_at": now,
                "updated_at": now,
                "procedure_code": request_body.procedure_code,
                "diagnosis_codes": request_body.diagnosis_codes,
                "insurance_plan": request_body.insurance_plan,
            }
        )

        if RUN_INLINE:
            # Explicit inline mode (local dev only — will timeout in Lambda)
            _run_graph_inline(request_body, request_id)
        elif AGENTCORE_RUNTIME_ARN:
            # Production: invoke AgentCore runtime asynchronously
            _invoke_agentcore(request_body, request_id)
        elif LAMBDA_FUNCTION_NAME:
            # Lambda without AgentCore: invoke self asynchronously as background worker
            _invoke_lambda_async(request_body, request_id)
        else:
            # Local dev fallback
            _run_graph_inline(request_body, request_id)

        return {
            "request_id": request_id,
            "status": "PROCESSING",
            "message": "Prior authorization request submitted. Poll /status/{request_id} for result.",
            "rate_usage": usage,
        }


def _run_graph_inline(request: PriorAuthRequest, request_id: str) -> None:
    """Run the auth graph synchronously (local dev / testing only)."""
    from authagent.graph.auth_graph import build_auth_graph
    graph = build_auth_graph()
    try:
        graph.run(request, request_id=request_id)
    except Exception as exc:
        update_status(request_id, "FAILED")
        raise RuntimeError(f"Graph execution failed: {exc}") from exc


def _invoke_agentcore(request: PriorAuthRequest, request_id: str) -> None:
    """Invoke AgentCore runtime asynchronously with the authorization payload."""
    payload = {
        "request_id": request_id,
        "request": request.model_dump(),
    }
    _bedrock_agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
        payload=json.dumps(payload),
    )


def _invoke_lambda_async(request: PriorAuthRequest, request_id: str) -> None:
    """
    Invoke this Lambda function asynchronously as a background worker.
    The worker event is detected by main.handler via the '_worker' key.
    API Gateway's 29 s timeout is bypassed — caller gets 202 immediately.
    """
    payload = {
        "_worker": True,
        "request_id": request_id,
        "request": request.model_dump(),
    }
    _lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="Event",  # async — no response body
        Payload=json.dumps(payload),
    )
