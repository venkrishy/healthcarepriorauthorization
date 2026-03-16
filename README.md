# AuthAgent — AWS-Native Healthcare Prior Authorization System

This solution has multiple AI agents running in AWS.  RAG is performed using AWS Bedrock Knowledge Bases.

It is a Multi-agent prior authorization system built on **AWS Strands Agents SDK** +
**Amazon Bedrock AgentCore** and uses **Claude Haiku Model**

It uses the Strands Sequential pipeline.

The agents are:
(1) OrchestratorAgent
(2) ClinicalGuidelinesAgent 
(3) MedicalNecessityAgent
(4) AuthorizationRouterAgent

Output will the final score.

The frontend for this can be seen at [Authorization Agent (theaiguru.dev) ](https://auth-agent.theaiguru.dev/).  
Built by [Venky Krishnaswamy](https://theaiguru.dev)

## Related to:
This is similar to the AI Agents `riskscout` and `crewmarketintelligence`: both are production-ready agentic systems and both are deployed on Azure Container Apps, they share FastAPI + telemetry best practices, and demonstrate multi-agent orchestration, observability, and deployment automation.

## Architecture

```
POST /authorize
       |
  API Gateway (Expected Usage Plan: 100/day, 3,100/month)
       |
  Lambda (Mangum + FastAPI)
       |
  Rate Limiter (DynamoDB atomic counters — Layer 2)
       |
  AgentCore Runtime
       |
  AuthGraph (Strands sequential pipeline)
       |
  OrchestratorAgent
       |
  ClinicalGuidelinesAgent  →  Bedrock Knowledge Bases (LCD/NCD RAG)
       |
  MedicalNecessityAgent    →  Score 0-100
       |
  AuthorizationRouterAgent →  APPROVED | PENDED | DENIED
       |
  DynamoDB (decisions + audit tables)
       |
  CloudWatch OTEL traces
```

**Decision logic:**
- `score >= 75` AND no missing info → `APPROVED`
- `score >= 75` AND missing info → `PENDED`
- `score < 75` → `DENIED`

## Setup

### Prerequisites
- AWS CLI configured (`aws configure`)
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- AWS CDK v2 (`npm install -g aws-cdk`)

### 1. Install dependencies

```bash
cd authagent
uv sync
```

### 2. Deploy infrastructure

```bash
cd infra
pip install -r requirements.txt  # CDK dependencies
cdk bootstrap
cdk deploy --all --require-approval never
```

Note the outputs:
- `KnowledgeBaseId` — set as `KNOWLEDGE_BASE_ID` in `.env`
- `GuidelinesBucketName` — for guidelines ingestion
- `AgentCoreRoleArn` — for AgentCore deployment

### 3. Copy and fill in environment variables

```bash
cp .env.example .env
# Edit .env with CDK output values
```

### 4. Ingest clinical guidelines

```bash
uv run python scripts/ingest_guidelines.py \
    --bucket authagent-guidelines-<account-id> \
    --kb-id <knowledge-base-id> \
    --data-source-id <data-source-id>
```

This uploads sample LCD/NCD/CPT guidelines and triggers Knowledge Base indexing.

### 5. Deploy AgentCore Runtime

```bash
uv run python scripts/deploy_agentcore.py \
    --bucket authagent-agentcore-deploy-<account-id> \
    --role-arn arn:aws:iam::<account>:role/authagent-agentcore-role \
    --kb-id <knowledge-base-id>
```

Copy the printed `Runtime ARN` and set `AGENTCORE_RUNTIME_ARN` in your env.

### 6. Local development

```bash
# Run inline (no AgentCore invocation):
RUN_INLINE=true uv run uvicorn authagent.api.main:app --reload --port 8000
```

## API Reference

### POST /authorize

Submit a prior authorization request.

```bash
curl -X POST https://<api-id>.execute-api.us-east-1.amazonaws.com/authorize \
  -H "Authorization: Bearer <cognito-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "PATIENT-001",
    "procedure_code": "45378",
    "diagnosis_codes": ["K57.30", "Z12.11"],
    "clinical_notes": "55-year-old with rectal bleeding and family history of CRC.",
    "requesting_provider": "NPI-1234567890",
    "insurance_plan": "BlueCross-PPO-Gold"
  }'
```

Response: `{"request_id": "uuid", "status": "PROCESSING"}`

### GET /status/{request_id}

```bash
curl https://<api>/status/uuid -H "Authorization: Bearer <token>"
```

### GET /decision/{request_id}

Returns full decision: score, guidelines cited, rationale, audit trail.

### GET /audit/{request_id}

Returns per-agent-turn audit with CloudWatch OTEL trace link.

## Sample Test Cases

### Case 1: APPROVED (Score >= 75, complete documentation)

```json
{
  "procedure_code": "45378",
  "diagnosis_codes": ["K57.30", "Z12.11"],
  "clinical_notes": "55-year-old patient with rectal bleeding x 3 months, family history of colorectal cancer (father diagnosed at 52). Previous colonoscopy 7 years ago showed no polyps. Physical exam unremarkable except guaiac positive stool.",
  "requesting_provider": "Gastroenterology Associates",
  "insurance_plan": "BlueCross-PPO-Gold"
}
```

### Case 2: PENDED (Score >= 75 but missing info)

```json
{
  "procedure_code": "27447",
  "diagnosis_codes": ["M17.11"],
  "clinical_notes": "Patient has knee pain. Reports difficulty walking.",
  "requesting_provider": "Orthopedic Surgery Center",
  "insurance_plan": "Aetna-HMO"
}
```
Expected: Missing PT notes, X-ray reports, conservative treatment documentation.

### Case 3: DENIED (Score < 75)

```json
{
  "procedure_code": "27447",
  "diagnosis_codes": ["M25.561"],
  "clinical_notes": "Patient wants knee replacement for mild discomfort.",
  "requesting_provider": "Primary Care Clinic",
  "insurance_plan": "Medicaid-Managed"
}
```
Expected: Indication not established, requesting provider not specialist.

## HIPAA Compliance Notes

- `patient_id` is SHA-256 hashed before any storage or telemetry
- Clinical notes are never written to OTEL spans — only metadata
- DynamoDB tables use AWS-managed encryption at rest
- All endpoints require Cognito JWT authentication
- Audit trail in DynamoDB + CloudWatch for compliance review

## Tests

```bash
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v
```

## Project Structure

```
authagent/
├── agents/          # Strands Agent definitions (4 agents)
├── graph/           # AuthGraph pipeline orchestration
├── tools/           # @tool functions: KB search, rate limiter, DynamoDB writes
├── api/             # FastAPI + Mangum Lambda handler
├── models/          # Pydantic schemas (request/response)
├── infra/           # AWS CDK stacks (5 stacks)
├── scripts/         # Ingestion + deployment scripts
└── tests/           # Unit + integration tests
```
