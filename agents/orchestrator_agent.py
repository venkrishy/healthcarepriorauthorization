"""
OrchestratorAgent — Graph driver.

Receives the raw PriorAuthRequest, formats the initial prompt for
the clinical guidelines agent, and collects the final structured
output from the authorization router agent.
"""

import json
import os

from strands import Agent
from strands.models import BedrockModel

from authagent.models.request import PriorAuthRequest

SYSTEM_PROMPT = """You are an orchestrator for a healthcare prior authorization multi-agent system.

Your role:
1. Receive a prior authorization request
2. Extract and format the key clinical information for downstream agents
3. Coordinate the analysis pipeline: guidelines -> necessity -> routing
4. Ensure the final decision is well-structured and complete

You do NOT make clinical judgments. You route information between specialist agents
and ensure the workflow completes with a structured output.

Output format for initial request to clinical agent:
{
  "request_id": "uuid",
  "procedure_code": "CPT-XXXXX",
  "diagnosis_codes": ["ICD10-X", "ICD10-Y"],
  "clinical_summary": "Sanitized summary without PHI",
  "requesting_provider": "Provider specialty/NPI",
  "insurance_plan": "Plan name"
}

HIPAA note: When summarizing clinical notes, remove any direct patient identifiers.
Reference the patient only by their hashed ID.
"""

_model = BedrockModel(
    model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

orchestrator_agent = Agent(
    model=_model,
    system_prompt=SYSTEM_PROMPT,
    tools=[],
)


def format_initial_message(request: PriorAuthRequest, request_id: str) -> str:
    """Format the PriorAuthRequest into an orchestrator-ready message."""
    return json.dumps(
        {
            "request_id": request_id,
            "procedure_code": request.procedure_code,
            "diagnosis_codes": request.diagnosis_codes,
            "clinical_notes": request.clinical_notes,  # agent will sanitize before passing down
            "requesting_provider": request.requesting_provider,
            "insurance_plan": request.insurance_plan,
        },
        indent=2,
    )
