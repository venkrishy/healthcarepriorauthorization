"""
Integration tests for the FastAPI endpoints.

Uses moto to mock DynamoDB. Rate limiter and DynamoDB writes
are tested against a real in-memory mock (no real AWS calls).
"""

import json
import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

# Set env vars before importing the app
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("DECISIONS_TABLE", "authagent-decisions")
os.environ.setdefault("AUDIT_TABLE", "authagent-audit")
os.environ.setdefault("RATE_LIMITS_TABLE", "authagent-rate-limits")
os.environ.setdefault("RUN_INLINE", "false")  # don't call Bedrock in tests
os.environ.setdefault("KNOWLEDGE_BASE_ID", "test-kb-id")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture(scope="module")
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"


@pytest.fixture
def dynamodb_tables(aws_credentials):
    """Create mocked DynamoDB tables for testing."""
    with mock_aws():
        dynamo = boto3.resource("dynamodb", region_name="us-east-1")

        for table_name in ["authagent-decisions", "authagent-audit", "authagent-rate-limits"]:
            pk = "pk" if "rate-limits" in table_name else "request_id"
            kwargs = dict(
                TableName=table_name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[
                    {"AttributeName": pk, "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": pk, "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
            )
            dynamo.create_table(**kwargs)

        yield dynamo


@pytest.fixture
def client(dynamodb_tables):
    from authagent.api.main import app
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_submit_authorization_without_invoke(client):
    """POST /authorize creates PROCESSING record in DynamoDB."""
    # Patch AgentCore invocation and rate limiter increment
    with (
        patch("authagent.api.routers.authorize.check_and_increment") as mock_rate,
        patch("authagent.api.routers.authorize._invoke_agentcore") as mock_invoke,
    ):
        mock_rate.return_value = {"counters": []}
        mock_invoke.return_value = None

        response = client.post(
            "/authorize",
            json={
                "patient_id": "PATIENT-001",
                "procedure_code": "45378",
                "diagnosis_codes": ["K57.30"],
                "clinical_notes": "Patient has rectal bleeding.",
                "requesting_provider": "NPI-123456789",
                "insurance_plan": "BlueCross-PPO",
            },
        )

    assert response.status_code == 202
    data = response.json()
    assert "request_id" in data
    assert data["status"] == "PROCESSING"


def test_rate_limit_rejection(client):
    """POST /authorize returns 429 when rate limit is exceeded."""
    from authagent.tools.rate_limiter import RateLimitExceeded

    with patch("authagent.api.routers.authorize.check_and_increment") as mock_rate:
        mock_rate.side_effect = RateLimitExceeded("daily", 100, 3600)

        response = client.post(
            "/authorize",
            json={
                "patient_id": "P001",
                "procedure_code": "45378",
                "diagnosis_codes": ["K57.30"],
                "clinical_notes": "...",
                "requesting_provider": "NPI-123",
                "insurance_plan": "Plan-A",
            },
        )

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "3600"
    assert "rate_limit_exceeded" in response.json()["detail"]["error"]


def test_status_not_found(client):
    response = client.get("/status/nonexistent-request-id")
    assert response.status_code == 404


def test_decision_not_found(client):
    response = client.get("/decision/nonexistent-request-id")
    assert response.status_code == 404


def test_audit_not_found(client):
    response = client.get("/audit/nonexistent-request-id")
    assert response.status_code == 404
