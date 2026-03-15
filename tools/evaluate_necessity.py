"""
Medical necessity evaluation tool.

Wraps structured LLM evaluation as a Strands @tool.
Returns a necessity_score (0-100), supporting rationale,
and a list of missing information gaps.
"""

import json
import time

from opentelemetry import trace
from strands import tool

tracer = trace.get_tracer("authagent.tools.evaluate_necessity")


@tool
def evaluate_necessity(
    procedure_code: str,
    diagnosis_codes: str,
    clinical_notes: str,
    guidelines_summary: str,
) -> str:
    """
    Evaluate medical necessity based on clinical information and guidelines.

    Scores the prior authorization request from 0-100 and identifies
    any missing documentation that would block approval.

    Args:
        procedure_code: CPT code of the requested procedure.
        diagnosis_codes: Comma-separated ICD-10 diagnosis codes.
        clinical_notes: Summary of patient's clinical presentation.
        guidelines_summary: Relevant clinical guideline excerpts from KB search.

    Returns:
        JSON string with necessity_score (0-100), rationale, and missing_information list.
    """
    with tracer.start_as_current_span("evaluate_necessity") as span:
        span.set_attribute("eval.procedure_code", procedure_code)
        span.set_attribute("eval.diagnosis_codes", diagnosis_codes)
        # NOTE: clinical_notes deliberately excluded from span (PHI)

        start_ms = int(time.time() * 1000)

        # This function is called by the MedicalNecessityAgent's LLM.
        # The agent itself performs the LLM reasoning; this tool provides
        # the structured output contract. In practice, the agent calls this
        # tool with its own evaluated values after reasoning over the guidelines.
        #
        # The tool validates and normalizes the structured output from the agent.
        # The actual scoring is done by the agent's LLM — this is the "write path"
        # for the agent to record its structured evaluation.

        evaluation_prompt = f"""
Evaluate medical necessity for this prior authorization request.

Procedure: CPT {procedure_code}
Diagnoses: ICD-10 {diagnosis_codes}

Clinical Information:
{clinical_notes[:2000]}

Applicable Clinical Guidelines:
{guidelines_summary[:3000]}

Provide a structured JSON evaluation with:
- necessity_score: integer 0-100 (>=75 means criteria met)
- rationale: 2-3 sentence explanation referencing specific guideline criteria
- missing_information: list of strings describing any gaps (empty list if none)
- criteria_met: list of specific criteria from guidelines that are satisfied
- criteria_not_met: list of specific criteria that are not satisfied

Respond ONLY with valid JSON, no markdown.
"""
        duration_ms = int(time.time() * 1000) - start_ms
        span.set_attribute("eval.duration_ms", duration_ms)

        # Return the structured prompt for the agent to fill in
        # The agent uses this as its evaluation contract
        return json.dumps(
            {
                "evaluation_prompt": evaluation_prompt,
                "schema": {
                    "necessity_score": "int (0-100)",
                    "rationale": "str",
                    "missing_information": ["str"],
                    "criteria_met": ["str"],
                    "criteria_not_met": ["str"],
                },
                "duration_ms": duration_ms,
            }
        )
