# Lesson: Strands Agents SDK Patterns for Multi-Agent Pipelines

## Architecture That Worked

A sequential pipeline where each agent builds on the previous agent's output:

```
OrchestratorAgent  →  ClinicalGuidelinesAgent  →  MedicalNecessityAgent  →  AuthorizationRouterAgent
(formats request)      (RAG search + summarize)    (score 0-100)             (write decision to DB)
```

Implemented as a Python class (`AuthGraph`) that calls agents in sequence and passes the
accumulated output as context to the next agent. No Strands `GraphBuilder` was needed —
plain function calls were simpler and more debuggable.

---

## Tool Definition Pattern

```python
from strands import tool

@tool
def search_guidelines(query: str, procedure_code: str = "", diagnosis_codes: str = "") -> str:
    """
    Search clinical guidelines in the Bedrock Knowledge Base.
    ... (full docstring — Strands sends this to the LLM as the tool description)
    """
    ...
    return json.dumps(results)  # always return JSON strings, not dicts
```

- All tool parameters must be JSON-serialisable primitives (str, int, float, bool).
- Return a JSON string, not a dict — Strands sends the return value as the tool result text.
- The docstring is critical: it's the LLM's only description of what the tool does.

---

## Agent Definition Pattern

```python
from strands import Agent
from strands.models import BedrockModel

model = BedrockModel(model_id="us.anthropic.claude-3-5-haiku-20241022-v1:0")

clinical_agent = Agent(
    model=model,
    system_prompt="You are a clinical guidelines specialist...",
    tools=[search_guidelines],
)

# Invoke:
response = clinical_agent(prompt_text)
output = str(response)  # convert AgentResult to string
```

---

## Extracting Structured Data from Agent Output

Agents return free-form text. Use regex to extract numeric scores reliably:

```python
import re

def _extract_necessity_score(text: str) -> int | None:
    for pattern in [
        r'"necessity_score"\s*:\s*(\d+)',
        r'necessity[_ ]score["\s:]+(\d+)',
        r'NECESSITY[_ ]SCORE["\s:=]+(\d+)',
        r'score["\s:]+(\d+)',
        r'\b(\d{2,3})\s*/\s*100\b',
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = int(match.group(1))
            if 0 <= val <= 100:
                return val
    return None
```

Include multiple patterns in order of specificity — agents vary their output format.

---

## Passing Context Between Agents

Use Python string formatting to pass previous agent output as context:

```python
necessity_prompt = f"""
Prior authorization request:
{format_initial_message(request)}

Clinical guidelines retrieved:
{clinical_output}

Evaluate medical necessity and return JSON with necessity_score (0-100).
"""
necessity_output = str(necessity_agent(necessity_prompt))
```

Each agent sees the full context from prior agents. This is simpler than shared state.

---

## System Prompt Design for Decision Agents

Be explicit about the JSON format you want. Agents are more reliable when the system prompt
specifies the exact field names:

```
You must respond with:
{
  "decision": "APPROVED" | "PENDED" | "DENIED",
  "necessity_score": <int 0-100 from MedicalNecessityAgent>,
  "rationale": "<reason>",
  "missing_information": ["<item1>", ...],
  "denial_reason": "<reason if DENIED, else empty string>"
}

Decision rules (strict):
- score >= 75 AND missing_information is empty → APPROVED
- score >= 75 AND missing_information has items → PENDED
- score < 75 → DENIED
```

---

## OTEL Tracing with Strands

Strands auto-instruments agent calls. Add custom spans for tool-level tracing:

```python
from opentelemetry import trace
tracer = trace.get_tracer("authagent.tools.search_guidelines")

with tracer.start_as_current_span("kb_retrieve") as span:
    span.set_attribute("kb.query", query)
    span.set_attribute("kb.knowledge_base_id", KNOWLEDGE_BASE_ID)
    # ... call Bedrock retrieve ...
    span.set_attribute("kb.results_count", len(results))
    span.set_attribute("kb.duration_ms", duration_ms)
```

Never set PHI in spans — only metadata (guideline IDs, procedure codes, scores, durations).
