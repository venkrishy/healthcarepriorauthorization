# AuthAgent: AWS-Native Healthcare Prior Authorization System

## Context

The original specification (prompt.txt) defines **AuthAgent** — a prior authorization system using AutoGen + Azure OpenAI + Azure AI Search + Cosmos DB. The user has decided to rebuild this on AWS using:
- **AWS Strands Agents SDK** with GraphBuilder for multi-agent orchestration
- **Amazon Bedrock AgentCore** for serverless, pay-as-you-go deployment
- **Amazon Cognito** for HIPAA-aligned identity management
- **OpenTelemetry via Strands** for full audit trail (which agent accessed which guideline, why each decision was reached)

This is a **portfolio project** (not production). Key constraints:
- **Model**: Claude 3 Haiku (cost-efficient; ~$0.25/$1.25 per million input/output tokens)
- **Hard limits**: Max **100 requests/day** and **3,100 requests/month**
- **Rate limiting**: Enforced at API Gateway (usage plans/quotas) AND in application code (DynamoDB counter)

This is a greenfield project — no code exists yet, only `prompt.txt` (requirements) and `docs/` (UI mockups).

---

## Agent Architecture

Four specialist agents connected by a Strands **Graph** pattern:

```
OrchestratorAgent
      │
      ▼
ClinicalGuidelinesAgent  ──→  (guidelines + citations returned)
      │
      ▼
MedicalNecessityAgent    ──→  (score 0-100 returned)
      │
      ▼
AuthorizationRouterAgent ──→  APPROVED | PENDED | DENIED
```

**Decision Logic** (unchanged from spec):
- Score >= 75 + no missing info -> `APPROVED`
- Score >= 75 + missing info   -> `PENDED`
- Score < 75                   -> `DENIED`

---

## Project Structure

```
authagent/
├── agents/
│   ├── __init__.py
│   ├── clinical_guidelines_agent.py    # RAG search via Bedrock Knowledge Bases
│   ├── medical_necessity_agent.py      # Score evaluation 0-100
│   ├── authorization_router_agent.py   # Decision routing
│   └── orchestrator_agent.py          # Drives graph, collects output
│
├── graph/
│   ├── __init__.py
│   └── auth_graph.py                  # GraphBuilder assembly + conditional edges
│
├── tools/
│   ├── __init__.py
│   ├── search_guidelines.py           # Bedrock Knowledge Bases retrieve tool
│   ├── rate_limiter.py                # DynamoDB atomic counter (daily/monthly hard limits)
│   ├── evaluate_necessity.py          # Structured scoring tool
│   └── write_decision.py             # DynamoDB persistence + audit tool
│
├── api/
│   ├── main.py                        # FastAPI app + Mangum adapter (Lambda handler)
│   └── routers/
│       ├── authorize.py               # POST /authorize
│       ├── status.py                  # GET /status/{request_id}
│       ├── decision.py                # GET /decision/{request_id}
│       └── audit.py                   # GET /audit/{request_id}
│
├── models/
│   ├── request.py                     # PriorAuthRequest (Pydantic)
│   └── response.py                    # AuthDecision, AuditTrail (Pydantic)
│
├── infra/                             # AWS CDK (Python)
│   ├── app.py
│   └── stacks/
│       ├── network_stack.py           # VPC, subnets
│       ├── auth_stack.py              # Cognito User Pool + Resource Server
│       ├── data_stack.py              # DynamoDB tables + Bedrock Knowledge Base
│       ├── api_stack.py               # API Gateway + Lambda + Usage Plan
│       └── agentcore_stack.py         # AgentCore Runtime + S3 deployment bucket
│
├── scripts/
│   ├── ingest_guidelines.py           # Upload LCD/NCD PDFs to S3, trigger KB sync
│   └── deploy_agentcore.py            # Zip + upload + create AgentCore runtime
│
├── tests/
│   ├── unit/
│   └── integration/
│
├── .bedrock_agentcore.yaml            # AgentCore CLI config
├── pyproject.toml                     # uv-managed dependencies
└── .env.example
```

---

## AWS Infrastructure Components

### 1. Amazon Bedrock AgentCore Runtime
- **Deployment**: ZIP package uploaded to S3, deployed via `agentcore launch` or boto3 `create_agent_runtime`
- **Architecture**: arm64 (Linux aarch64), Python 3.13
- **Entry point**: `opentelemetry-instrument main.py` (enables OTEL auto-instrumentation)
- **Session config**: `idleRuntimeSessionTimeout: 300`, `maxLifetime: 1800`
- **Scaling**: Serverless, pay-per-invocation — no infrastructure to manage

### 2. Strands GraphBuilder (Multi-Agent Orchestration)
```python
# graph/auth_graph.py
from strands import Agent
from strands.multiagent import GraphBuilder

def build_auth_graph():
    graph = (
        GraphBuilder()
        .add_node("orchestrator", orchestrator_agent)
        .add_node("clinical", clinical_guidelines_agent)
        .add_node("necessity", medical_necessity_agent)
        .add_node("router", authorization_router_agent)
        .add_edge("orchestrator", "clinical")
        .add_edge("clinical", "necessity")
        .add_conditional_edge(
            "necessity",
            "router",
            condition=lambda state: state["necessity_score"] is not None
        )
        .set_entry_point("orchestrator")
        .set_finish_point("router")
        .build()
    )
    return graph
```

### 3. Strands Agents (LLM: Claude 3 Haiku on Bedrock)
Each agent is defined with `@tool`-decorated functions:
```python
from strands import Agent, tool
from strands.models import BedrockModel

model = BedrockModel(model_id="anthropic.claude-3-haiku-20240307-v1:0")

clinical_guidelines_agent = Agent(
    model=model,
    system_prompt="You are a clinical guidelines specialist...",
    tools=[search_guidelines],  # calls Bedrock Knowledge Bases retrieve
)
```

### 4. Amazon Cognito (HIPAA-Aligned Identity)
- **User Pool**: MFA enabled, password policy, no PHI in attributes
- **Resource Server**: Custom scopes (`authagent/submit`, `authagent/read`, `authagent/audit`)
- **App Client**: Client credentials flow for service-to-service (Lambda -> AgentCore)
- **JWT validation**: API Gateway authorizer validates Cognito JWTs on every request
- **HIPAA**: BAA with AWS required; Cognito is HIPAA-eligible

### 5. Amazon Bedrock Knowledge Bases (Clinical Guidelines RAG — fully serverless)
- **Replaces**: Raw OpenSearch Serverless (eliminates ~$350/month minimum OCU cost)
- **Storage**: Clinical guidelines (LCD/NCD PDFs, CPT/ICD-10 policies) in S3
- **Embeddings**: Titan Embeddings v2 (dim=1536), managed automatically by Knowledge Bases
- **Query**: `retrieve()` API call — billed per query, no idle cost
- **Strands tool**: Custom boto3 `retrieve` call wrapped as `@tool`
- **Ingestion**: `scripts/ingest_guidelines.py` uploads PDFs to S3, triggers KB sync
- **Security**: S3 SSE-S3 or KMS encryption; IAM policy scoped to Lambda execution role

### 6. Rate Limiting (Two-Layer Defense)

#### Layer 1 — API Gateway Usage Plans
```
Throttle:  10 req/second burst, 2 req/second steady rate
Quota:     100 requests/day, 3,100 requests/month
```
- Create an **API key** + **Usage Plan** in CDK
- API Gateway returns HTTP 429 automatically when quota is exceeded
- No code change needed; enforced at the network edge

#### Layer 2 — Application Code (DynamoDB Atomic Counters)
Double-enforcement in the Lambda handler before invoking AgentCore:
```python
# tools/rate_limiter.py
import boto3, datetime
from botocore.exceptions import ClientError

def check_and_increment(table):
    today = datetime.date.today().isoformat()
    month = today[:7]  # "YYYY-MM"

    # Atomic conditional increment — fails if limit reached
    for pk, limit in [(f"DAILY#{today}", 100), (f"MONTHLY#{month}", 3100)]:
        table.update_item(
            Key={"pk": pk, "sk": "COUNTER"},
            UpdateExpression="ADD #count :one",
            ConditionExpression="#count < :limit",
            ExpressionAttributeNames={"#count": "count"},
            ExpressionAttributeValues={":one": 1, ":limit": limit},
        )
    # TTL: daily keys expire after 2 days, monthly after 35 days
```
- Returns HTTP 429 with `Retry-After` header if either limit is hit
- Atomic update prevents race conditions (no separate read-then-write)
- DynamoDB conditional update is the guard; API Gateway quota is the first line

**Table: `authagent-rate-limits`** — separate from decisions table, TTL-enabled

---

### 7. Amazon DynamoDB (Decision Persistence)
**Table: `authagent-decisions`**
- PK: `request_id` (UUID)
- SK: `METADATA`
- Attributes: `decision`, `necessity_score`, `guidelines_cited[]`, `rationale`, `missing_information[]`, `denial_reason`, `audit_trail[]`, `created_at`, `patient_id_hash` (SHA-256, not raw PHI)

**Table: `authagent-audit`**
- PK: `request_id`
- SK: `agent_name#timestamp`
- Attributes: `agent_name`, `action`, `guideline_id`, `reasoning`, `duration_ms`, `trace_id`

### 8. OpenTelemetry Audit Trail (via Strands + AgentCore Observability)
Strands has native OpenTelemetry integration. Each agent turn emits spans automatically:
```python
# AgentCore Runtime entry point enables OTEL auto-instrumentation:
# entryPoint: ['opentelemetry-instrument', 'main.py']

# Custom spans for clinical guideline access:
from opentelemetry import trace
tracer = trace.get_tracer("authagent.clinical")

with tracer.start_as_current_span("guideline_lookup") as span:
    span.set_attribute("agent.name", "ClinicalGuidelinesAgent")
    span.set_attribute("guideline.id", guideline_id)
    span.set_attribute("guideline.policy_type", "LCD")
    span.set_attribute("decision.rationale", rationale_snippet)
    span.set_attribute("request.id", request_id)
```
- **Backend**: CloudWatch Transaction Search (AgentCore Observability)
- **Audit query**: Filter by `request_id` -> see every agent's span, which guideline was accessed, why decision was reached
- **HIPAA**: Spans contain only metadata — no raw clinical notes in traces

### 9. API Gateway + Lambda
- **FastAPI** with `Mangum` adapter as Lambda handler
- **API Gateway v2** (HTTP API) with JWT Cognito authorizer + Usage Plan (rate limits)
- **Lambda**: `arm64`, Python 3.13, 512MB RAM, 30s timeout
- **Endpoints**:
  - `POST /authorize` — validates request, checks rate limits, invokes AgentCore, returns `request_id`
  - `GET /status/{request_id}` — polls DynamoDB for current status
  - `GET /decision/{request_id}` — returns full decision with guidelines cited
  - `GET /audit/{request_id}` — returns full agent conversation + OTEL trace links

### 10. AWS CDK Stacks (Python, IaC)
- `NetworkStack` -> VPC, private subnets, VPC endpoints (DynamoDB, Bedrock, S3)
- `AuthStack` -> Cognito User Pool, Resource Server, App Client
- `DataStack` -> DynamoDB tables (PAY_PER_REQUEST), Bedrock Knowledge Base, S3 guidelines bucket
- `ApiStack` -> Lambda function, API Gateway HTTP API, Cognito JWT authorizer, API Key + Usage Plan
- `AgentCoreStack` -> S3 deployment bucket, AgentCore IAM execution role

---

## Data Models

### Input (PriorAuthRequest)
```python
class PriorAuthRequest(BaseModel):
    patient_id: str          # hashed before logging
    procedure_code: str      # CPT
    diagnosis_codes: list[str]  # ICD-10
    clinical_notes: str      # scrubbed before telemetry
    requesting_provider: str
    insurance_plan: str
```

### Output (AuthDecision)
```python
class AuthDecision(BaseModel):
    request_id: str
    decision: Literal["APPROVED", "PENDED", "DENIED"]
    necessity_score: int     # 0-100
    guidelines_cited: list[GuidelineRef]
    rationale: str
    missing_information: list[str]
    denial_reason: Optional[str]
    audit_trail: list[AgentTurn]
    trace_id: str            # links to CloudWatch OTEL trace
```

---

## HIPAA Compliance

| Requirement | Implementation |
|------------|----------------|
| PHI in logs | `patient_id` SHA-256 hashed before any logging/telemetry |
| Clinical notes | Stripped from OTEL spans; only metadata in traces |
| Encryption at rest | DynamoDB + S3 SSE with AWS-managed keys |
| Encryption in transit | TLS enforced on all endpoints |
| Access control | Cognito JWT scopes on all API routes |
| Audit trail | DynamoDB audit table + CloudWatch OTEL traces per decision |
| BAA | Required with AWS before production |

---

## Serverless Analysis

### Truly serverless (scale-to-zero)

| Component | Billing Model |
|-----------|--------------|
| AgentCore Runtime | Per-second active CPU/memory; I/O wait is free |
| AWS Lambda | Per-invocation + GB-seconds; free tier included |
| API Gateway (HTTP) | Per request ($1.00/million) |
| DynamoDB PAY_PER_REQUEST | Per read/write unit; $0 at zero traffic |
| Bedrock Knowledge Bases | Per retrieval query; $0 at zero traffic |
| Amazon Cognito | Per MAU; first 10,000 free |
| CloudWatch / OTEL | Per GB ingested; $0 at zero traffic |

All components are **100% serverless / scale-to-zero**. OpenSearch Serverless was explicitly avoided due to its ~$350/month minimum OCU billing floor.

---

## Cost Estimate

Assumptions: **Max 3,100 requests/month** (hard cap enforced), US East (N. Virginia). Model: **Claude 3 Haiku**.

### Monthly Cost Summary

| Service | Monthly Cost |
|---------|-------------|
| Bedrock Claude 3 Haiku (~37M input + 6M output tokens) | ~$17 |
| AgentCore Runtime (~28K active compute seconds) | ~$2–5 |
| Bedrock Knowledge Bases (~12K queries) | < $1 |
| DynamoDB (PAY_PER_REQUEST) | < $1 |
| Lambda + API Gateway | ~$0 |
| Cognito | ~$0 |
| CloudWatch + OTEL (~12MB traces) | < $1 |
| S3 (guidelines + deployment package) | ~$1 |
| **Total** | **~$20–25/month** |

Per-request breakdown: ~$17 Bedrock / 3,100 = **~$0.0055 per authorization** (~half a cent).

---

## Key Dependencies (pyproject.toml)

```toml
[project]
dependencies = [
    "strands-agents>=0.1.0",
    "strands-agents-tools>=0.1.0",
    "fastapi>=0.115.0",
    "mangum>=0.19.0",
    "boto3>=1.38.0",
    "opentelemetry-api>=1.30.0",
    "opentelemetry-sdk>=1.30.0",
    "opentelemetry-instrumentation>=0.51b0",
    "pydantic>=2.10.0",
]
```

---

## Build Sequence

1. **Infra first**: `cdk deploy --all` -> Cognito, DynamoDB, Knowledge Base, API Gateway
2. **Ingest guidelines**: `python scripts/ingest_guidelines.py` -> upload LCD/NCD PDFs -> S3 -> KB sync
3. **Build agents**: implement tools -> agents -> graph
4. **API layer**: FastAPI routers + Mangum adapter + rate limiter
5. **Deploy AgentCore**: `agentcore launch` or `python scripts/deploy_agentcore.py`
6. **Wire API -> AgentCore**: Lambda invokes AgentCore runtime endpoint
7. **Configure observability**: Enable CloudWatch Transaction Search in AgentCore console

---

## Verification

- **Unit tests**: Mock Bedrock calls, validate agent tool I/O, decision logic thresholds, rate limiter counter logic
- **Integration tests**: Real DynamoDB (DynamoDB Local), mock Knowledge Base responses
- **E2E test**: Submit a sample prior auth request -> check `GET /decision/{id}` returns correct `APPROVED/PENDED/DENIED` + `GET /audit/{id}` returns full trace with guideline citations
- **Rate limit test**: Send 101 requests in a day -> verify 101st returns HTTP 429
- **HIPAA check**: Confirm `patient_id` never appears raw in CloudWatch logs or OTEL spans
- **Observability check**: Open CloudWatch Transaction Search -> filter by `request_id` -> verify each agent span shows `guideline.id`, `agent.name`, and `decision.rationale`

---

## Files to Create

| File | Purpose |
|------|---------|
| `agents/clinical_guidelines_agent.py` | RAG agent with Bedrock KB tool |
| `agents/medical_necessity_agent.py` | Scoring agent |
| `agents/authorization_router_agent.py` | Decision routing agent |
| `agents/orchestrator_agent.py` | Graph driver |
| `graph/auth_graph.py` | GraphBuilder assembly |
| `tools/search_guidelines.py` | Bedrock Knowledge Bases retrieve |
| `tools/rate_limiter.py` | DynamoDB atomic counter for daily/monthly hard limits |
| `tools/write_decision.py` | DynamoDB persistence + audit |
| `api/main.py` | FastAPI + Mangum entry point |
| `api/routers/*.py` | 4 route handlers |
| `models/request.py` | PriorAuthRequest schema |
| `models/response.py` | AuthDecision, AuditTrail schemas |
| `infra/app.py` + `stacks/*.py` | CDK infrastructure (5 stacks) |
| `scripts/ingest_guidelines.py` | Clinical data ingestion |
| `scripts/deploy_agentcore.py` | AgentCore deployment helper |
| `.bedrock_agentcore.yaml` | AgentCore CLI config |
| `pyproject.toml` | uv-managed dependencies |
