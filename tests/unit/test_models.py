"""Unit tests for Pydantic data models."""

import pytest
from pydantic import ValidationError

from authagent.models.request import PriorAuthRequest
from authagent.models.response import AuthDecision, AuthStatus, AuditTrail, AgentTurn, GuidelineRef


def test_prior_auth_request_valid():
    req = PriorAuthRequest(
        patient_id="P12345",
        procedure_code="45378",
        diagnosis_codes=["K57.30", "Z12.11"],
        clinical_notes="Patient presents with rectal bleeding and family history of CRC.",
        requesting_provider="NPI-1234567890",
        insurance_plan="BlueCross-PPO-Gold",
    )
    assert req.procedure_code == "45378"
    assert len(req.diagnosis_codes) == 2


def test_prior_auth_request_missing_required_field():
    with pytest.raises(ValidationError):
        PriorAuthRequest(
            patient_id="P12345",
            # missing procedure_code
            diagnosis_codes=["K57.30"],
            clinical_notes="...",
            requesting_provider="NPI-123",
            insurance_plan="Plan-A",
        )


def test_auth_decision_valid():
    decision = AuthDecision(
        request_id="req-uuid-001",
        decision="APPROVED",
        necessity_score=85,
        guidelines_cited=[
            GuidelineRef(
                guideline_id="LCD-L35539",
                title="Colonoscopy LCD",
                policy_type="LCD",
                relevance_score=0.92,
                excerpt="Coverage criteria excerpt...",
            )
        ],
        rationale="Request meets all criteria per LCD L35539.",
        missing_information=[],
        denial_reason=None,
        audit_trail=[
            AgentTurn(
                agent_name="ClinicalGuidelinesAgent",
                action="search_guidelines",
                reasoning="Found LCD-L35539 relevant to CPT 45378",
                guideline_ids=["LCD-L35539"],
                duration_ms=1200,
                timestamp="2026-03-14T00:00:00Z",
                trace_id="0x" + "0" * 32,
            )
        ],
        trace_id="0x" + "0" * 32,
        created_at="2026-03-14T00:00:00Z",
        duration_ms=5000,
    )
    assert decision.decision == "APPROVED"
    assert decision.necessity_score == 85


def test_necessity_score_bounds():
    with pytest.raises(ValidationError):
        AuthDecision(
            request_id="r",
            decision="APPROVED",
            necessity_score=101,  # > 100 — invalid
            guidelines_cited=[],
            rationale="",
            missing_information=[],
            audit_trail=[],
            trace_id="",
            created_at="",
            duration_ms=0,
        )
