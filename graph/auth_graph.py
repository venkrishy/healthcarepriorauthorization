"""
AuthGraph — Strands GraphBuilder multi-agent pipeline.

Pipeline: orchestrator -> clinical -> necessity -> router
Each node is a Strands Agent. The graph passes shared state
between nodes via the AgentState dict.

Note on Strands Graph API:
  Strands multiagent.Graph uses message passing between agents.
  Each agent receives the accumulated conversation and adds its response.
  The graph enforces the sequential execution order defined here.
"""

import json
import time
import uuid
from typing import Any

from opentelemetry import trace

from authagent.agents.clinical_guidelines_agent import clinical_guidelines_agent
from authagent.agents.medical_necessity_agent import medical_necessity_agent
from authagent.agents.authorization_router_agent import authorization_router_agent
from authagent.agents.orchestrator_agent import orchestrator_agent, format_initial_message
from authagent.models.request import PriorAuthRequest

tracer = trace.get_tracer("authagent.graph")


class AuthGraph:
    """
    Sequential multi-agent pipeline for prior authorization.

    Implements the Strands agent communication pattern manually
    to support structured state passing between agents and
    fine-grained OpenTelemetry instrumentation per agent turn.
    """

    def __init__(self):
        self.agents = {
            "orchestrator": orchestrator_agent,
            "clinical": clinical_guidelines_agent,
            "necessity": medical_necessity_agent,
            "router": authorization_router_agent,
        }

    def run(self, request: PriorAuthRequest, request_id: str | None = None) -> dict[str, Any]:
        """
        Execute the full prior authorization pipeline.

        Args:
            request: Validated PriorAuthRequest.
            request_id: Optional UUID; generated if not provided.

        Returns:
            Final AuthDecision dict from the router agent.
        """
        request_id = request_id or str(uuid.uuid4())
        pipeline_start_ms = int(time.time() * 1000)
        audit_trail = []

        with tracer.start_as_current_span("auth_pipeline") as root_span:
            root_span.set_attribute("request.id", request_id)
            root_span.set_attribute("request.procedure_code", request.procedure_code)
            root_span.set_attribute("request.insurance_plan", request.insurance_plan)
            # patient_id deliberately excluded from spans (PHI)

            # --- Step 1: Orchestrator ---
            orchestrator_result, orchestrator_turn = self._run_agent(
                agent_name="OrchestratorAgent",
                agent=self.agents["orchestrator"],
                message=format_initial_message(request, request_id),
                request_id=request_id,
            )
            audit_trail.append(orchestrator_turn)

            # --- Step 2: Clinical Guidelines ---
            clinical_message = (
                f"Prior auth request has been received. Please search the clinical guidelines "
                f"knowledge base for CPT {request.procedure_code} with diagnoses "
                f"{', '.join(request.diagnosis_codes)}.\n\n"
                f"Orchestrator context:\n{orchestrator_result}"
            )
            clinical_result, clinical_turn = self._run_agent(
                agent_name="ClinicalGuidelinesAgent",
                agent=self.agents["clinical"],
                message=clinical_message,
                request_id=request_id,
            )
            audit_trail.append(clinical_turn)

            # --- Step 3: Medical Necessity ---
            necessity_message = (
                f"Please evaluate medical necessity for this prior authorization request.\n\n"
                f"Clinical guidelines retrieved:\n{clinical_result}\n\n"
                f"Patient clinical notes (for necessity scoring only):\n{request.clinical_notes[:2000]}\n\n"
                f"Procedure: CPT {request.procedure_code}, Diagnoses: {', '.join(request.diagnosis_codes)}"
            )
            necessity_result, necessity_turn = self._run_agent(
                agent_name="MedicalNecessityAgent",
                agent=self.agents["necessity"],
                message=necessity_message,
                request_id=request_id,
            )
            audit_trail.append(necessity_turn)

            # Extract necessity_score for conditional edge logic
            necessity_score = self._extract_necessity_score(necessity_result)
            root_span.set_attribute("decision.necessity_score", necessity_score)

            # --- Step 4: Authorization Router (conditional edge: score is not None) ---
            if necessity_score is not None:
                router_message = (
                    f"Please route this authorization decision and persist the result.\n\n"
                    f"Request ID: {request_id}\n"
                    f"Patient ID (for hashing): {request.patient_id}\n"
                    f"Procedure: CPT {request.procedure_code}\n\n"
                    f"Medical necessity evaluation:\n{necessity_result}\n\n"
                    f"Clinical guidelines cited:\n{clinical_result}\n\n"
                    f"Audit trail so far: {json.dumps(audit_trail)}\n"
                    f"Trace ID: {root_span.get_span_context().trace_id:#034x}"
                )
                router_result, router_turn = self._run_agent(
                    agent_name="AuthorizationRouterAgent",
                    agent=self.agents["router"],
                    message=router_message,
                    request_id=request_id,
                )
                audit_trail.append(router_turn)
            else:
                raise RuntimeError("MedicalNecessityAgent did not return a necessity_score")

            total_duration_ms = int(time.time() * 1000) - pipeline_start_ms
            root_span.set_attribute("pipeline.duration_ms", total_duration_ms)
            root_span.set_attribute("pipeline.audit_turns", len(audit_trail))

            return {
                "request_id": request_id,
                "router_output": router_result,
                "audit_trail": audit_trail,
                "necessity_score": necessity_score,
                "trace_id": f"{root_span.get_span_context().trace_id:#034x}",
                "duration_ms": total_duration_ms,
            }

    def _run_agent(
        self,
        agent_name: str,
        agent: Any,
        message: str,
        request_id: str,
    ) -> tuple[str, dict]:
        """Run a single agent and return (output_text, audit_turn_dict)."""
        with tracer.start_as_current_span(f"agent.{agent_name.lower()}") as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("request.id", request_id)

            start_ms = int(time.time() * 1000)
            result = agent(message)
            duration_ms = int(time.time() * 1000) - start_ms

            output_text = str(result)
            span.set_attribute("agent.duration_ms", duration_ms)
            span.set_attribute("agent.output_length", len(output_text))

            import datetime
            turn = {
                "agent_name": agent_name,
                "action": f"{agent_name} analysis",
                "reasoning": output_text[:500],  # truncated for audit — no PHI
                "guideline_ids": self._extract_guideline_ids(output_text),
                "duration_ms": duration_ms,
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "trace_id": f"{span.get_span_context().trace_id:#034x}",
            }
            return output_text, turn

    @staticmethod
    def _extract_necessity_score(text: str) -> int | None:
        """Parse necessity_score from agent output text."""
        import re
        match = re.search(r'"necessity_score"\s*:\s*(\d+)', text)
        if match:
            return int(match.group(1))
        match = re.search(r'necessity_score[:\s]+(\d+)', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _extract_guideline_ids(text: str) -> list[str]:
        """Extract guideline IDs from agent output."""
        import re
        ids = re.findall(r'(?:LCD|NCD|CPT|ICD10|L\d{5}|L\d{4})[A-Z0-9\-\.]*', text)
        return list(set(ids))[:10]  # cap at 10


def build_auth_graph() -> AuthGraph:
    """Factory function — returns a ready-to-run AuthGraph instance."""
    return AuthGraph()
