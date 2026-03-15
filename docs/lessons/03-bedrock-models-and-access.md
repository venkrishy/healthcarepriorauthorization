# Lesson: Bedrock Model Access — Marketplace vs Cross-Region Inference Profiles

## The Problem

`anthropic.claude-3-haiku-20240307-v1:0` threw `AccessDeniedException`:

```
An error occurred (AccessDeniedException) when calling the InvokeModel operation:
Your account is not subscribed to this model. Please subscribe to this model in the
Amazon Bedrock console.
```

The AWS Marketplace agreement screen blocked activation.

---

## Root Cause

Some Bedrock model IDs require an explicit opt-in via the AWS Marketplace. The original
Claude 3 Haiku base model (`anthropic.claude-3-haiku-20240307-v1:0`) requires this.

**Cross-region inference profiles do NOT require Marketplace opt-in.** They are activated
automatically for any account with Bedrock access.

---

## Model ID Reference

| Model | ID | Marketplace Required |
|-------|-----|---------------------|
| Claude 3 Haiku (base) | `anthropic.claude-3-haiku-20240307-v1:0` | Yes |
| Claude 3.5 Haiku (cross-region) | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | No |
| Claude 3.5 Sonnet (cross-region) | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` | No |
| Claude 3 Opus (cross-region) | `us.anthropic.claude-3-opus-20240229-v1:0` | No |

Prefix `us.` = US cross-region inference profile. Also available: `eu.` and `ap.`.

---

## Rule: Always Use Cross-Region Inference Profiles by Default

```python
# In CDK / env vars:
BEDROCK_MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"

# In Strands:
from strands.models import BedrockModel
model = BedrockModel(model_id="us.anthropic.claude-3-5-haiku-20241022-v1:0")
```

Cross-region profiles also provide higher throughput and automatic failover across regions —
strictly better than single-region base model IDs for production use.

---

## How to Check Available Models in Your Account

```bash
aws bedrock list-foundation-models \
  --query "modelSummaries[?contains(modelId,'haiku')].{id:modelId,status:modelLifecycle.status}" \
  --output table

# Or check a specific model's access:
aws bedrock get-foundation-model --model-identifier us.anthropic.claude-3-5-haiku-20241022-v1:0
```

---

## Cost Note (Claude 3.5 Haiku vs Claude 3 Haiku)

| Model | Input | Output |
|-------|-------|--------|
| Claude 3 Haiku | $0.25/1M | $1.25/1M |
| Claude 3.5 Haiku | $0.80/1M | $4.00/1M |

For a portfolio project capped at 3,100 req/month with ~14K tokens/request:
- Claude 3 Haiku: ~$17/month
- Claude 3.5 Haiku: ~$50/month

If cost is primary concern and Marketplace opt-in is possible, Claude 3 Haiku is cheaper.
Otherwise Claude 3.5 Haiku cross-region is the frictionless default.
