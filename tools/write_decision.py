"""
DynamoDB persistence tool for authorization decisions and audit trail.

Writes to two tables:
  authagent-decisions  — final decision record per request
  authagent-audit      — per-agent-turn audit entries
"""

import datetime
import hashlib
import json
import os
import time

import boto3
from opentelemetry import trace
from strands import tool

tracer = trace.get_tracer("authagent.tools.write_decision")

DECISIONS_TABLE = os.environ.get("DECISIONS_TABLE", "authagent-decisions")
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "authagent-audit")

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _hash_patient_id(patient_id: str) -> str:
    """SHA-256 hash of patient_id — never log raw PHI."""
    return hashlib.sha256(patient_id.encode()).hexdigest()


@tool
def write_decision(
    request_id: str,
    patient_id: str,
    decision: str,
    necessity_score: int,
    guidelines_cited: str,
    rationale: str,
    missing_information: str,
    denial_reason: str,
    audit_trail: str,
    trace_id: str,
    duration_ms: int,
) -> str:
    """
    Persist the final authorization decision to DynamoDB.

    Stores the decision in authagent-decisions and individual agent turns
    in authagent-audit. The patient_id is hashed (SHA-256) before storage.

    Args:
        request_id: Unique UUID for this authorization request.
        patient_id: Raw patient identifier (will be hashed before storage).
        decision: One of "APPROVED", "PENDED", or "DENIED".
        necessity_score: Integer 0-100 from MedicalNecessityAgent.
        guidelines_cited: JSON array of GuidelineRef objects.
        rationale: Human-readable explanation of the decision.
        missing_information: JSON array of missing info strings (may be empty).
        denial_reason: Reason if DENIED, empty string otherwise.
        audit_trail: JSON array of AgentTurn objects.
        trace_id: OpenTelemetry trace ID for CloudWatch correlation.
        duration_ms: Total processing time in milliseconds.

    Returns:
        JSON string with write confirmation and DynamoDB item key.
    """
    with tracer.start_as_current_span("write_decision") as span:
        span.set_attribute("decision.request_id", request_id)
        span.set_attribute("decision.outcome", decision)
        span.set_attribute("decision.necessity_score", necessity_score)
        span.set_attribute("decision.trace_id", trace_id)
        # patient_id is hashed — never in spans

        now = datetime.datetime.utcnow().isoformat() + "Z"
        patient_id_hash = _hash_patient_id(patient_id)

        decisions_table = _dynamodb.Table(DECISIONS_TABLE)
        start_ms = int(time.time() * 1000)

        decisions_table.put_item(
            Item={
                "request_id": request_id,
                "sk": "METADATA",
                "decision": decision,
                "necessity_score": necessity_score,
                "guidelines_cited": json.loads(guidelines_cited) if guidelines_cited else [],
                "rationale": rationale,
                "missing_information": json.loads(missing_information) if missing_information else [],
                "denial_reason": denial_reason or None,
                "audit_trail": json.loads(audit_trail) if audit_trail else [],
                "trace_id": trace_id,
                "patient_id_hash": patient_id_hash,
                "created_at": now,
                "status": "COMPLETED",
                "duration_ms": duration_ms,
            }
        )

        # Write individual audit entries for each agent turn
        audit_table = _dynamodb.Table(AUDIT_TABLE)
        turns = json.loads(audit_trail) if audit_trail else []
        with audit_table.batch_writer() as batch:
            for turn in turns:
                batch.put_item(
                    Item={
                        "request_id": request_id,
                        "sk": f"{turn.get('agent_name', 'unknown')}#{turn.get('timestamp', now)}",
                        "agent_name": turn.get("agent_name", ""),
                        "action": turn.get("action", ""),
                        "reasoning": turn.get("reasoning", ""),
                        "guideline_ids": turn.get("guideline_ids", []),
                        "duration_ms": turn.get("duration_ms", 0),
                        "trace_id": turn.get("trace_id", trace_id),
                        "timestamp": turn.get("timestamp", now),
                    }
                )

        write_duration_ms = int(time.time() * 1000) - start_ms
        span.set_attribute("write.duration_ms", write_duration_ms)

        return json.dumps(
            {
                "status": "written",
                "request_id": request_id,
                "decision": decision,
                "patient_id_hash": patient_id_hash,
                "write_duration_ms": write_duration_ms,
                "audit_entries_written": len(turns),
            }
        )


def update_status(request_id: str, status: str) -> None:
    """Update the processing status of an in-flight request."""
    decisions_table = _dynamodb.Table(DECISIONS_TABLE)
    now = datetime.datetime.utcnow().isoformat() + "Z"
    decisions_table.update_item(
        Key={"request_id": request_id, "sk": "METADATA"},
        UpdateExpression="SET #status = :status, updated_at = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": status, ":now": now},
    )
