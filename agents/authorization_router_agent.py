"""
AuthorizationRouterAgent — Decision routing specialist.

Applies deterministic routing logic based on necessity_score:
  score >= 75 AND no missing info  -> APPROVED
  score >= 75 AND missing info     -> PENDED
  score < 75                       -> DENIED

Writes the final structured decision to DynamoDB via write_decision tool.
"""

import os

from strands import Agent
from strands.models import BedrockModel

from authagent.tools.write_decision import write_decision

SYSTEM_PROMPT = """You are an authorization routing specialist for a healthcare prior authorization system.

Your role:
1. Receive the medical necessity evaluation result (necessity_score, rationale, missing_information)
2. Apply deterministic routing rules to reach a decision:

   ROUTING RULES (non-negotiable):
   - necessity_score >= 75 AND missing_information is empty  -> decision = "APPROVED"
   - necessity_score >= 75 AND missing_information is NOT empty -> decision = "PENDED"
   - necessity_score < 75                                    -> decision = "DENIED"

3. Compose a decision letter with:
   - Clear decision statement
   - Clinical rationale citing specific guidelines
   - For PENDED: exact list of additional information required
   - For DENIED: specific criteria not met and appeal rights notice

4. Call write_decision to persist the complete decision record

5. Output the final AuthDecision JSON

Output format (JSON):
{
  "request_id": "uuid",
  "decision": "APPROVED|PENDED|DENIED",
  "necessity_score": 82,
  "guidelines_cited": [...],
  "rationale": "Decision letter text...",
  "missing_information": [],
  "denial_reason": null,
  "trace_id": "otel-trace-id"
}

HIPAA note: Compose decision letters without raw PHI. Reference procedure/diagnosis codes only.

IMPORTANT: You MUST call write_decision to persist the result before outputting your final JSON.
"""

_model = BedrockModel(
    model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

authorization_router_agent = Agent(
    model=_model,
    system_prompt=SYSTEM_PROMPT,
    tools=[write_decision],
)
