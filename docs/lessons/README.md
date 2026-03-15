# Lessons Learned: AWS Agent System (AuthAgent)

Hard-won lessons from building an AWS-native multi-agent prior authorization system using
Strands Agents, Bedrock, CDK Python, Lambda, Cognito, and DynamoDB.

---

## Lessons Index

| File | Topic | TL;DR |
|------|-------|-------|
| [01](01-cdk-python-lambda-packaging.md) | CDK Lambda Packaging | `entry` = parent of package; `requirements.txt` not `pyproject.toml`; set `output` outside project |
| [02](02-cognito-client-credentials-jwt.md) | Cognito JWT / API Gateway | Client credentials tokens have no `aud`; use App Client ID as `jwt_audience`; App Client ID is public |
| [03](03-bedrock-models-and-access.md) | Bedrock Model Access | Use `us.` cross-region profiles; base model IDs require Marketplace opt-in |
| [04](04-api-gateway-timeout-async-pattern.md) | API Gateway 29s Timeout | Return 202 immediately; invoke Lambda async as background worker; set Lambda timeout to 5 min |
| [05](05-serverless-rag-bedrock-kb-vs-aoss.md) | Serverless RAG | AOSS = $350/month minimum; Bedrock Knowledge Bases = $0 idle, pay-per-query |
| [06](06-cdk-gotchas.md) | CDK Gotchas | Circular deps, RETAIN orphans, scope objects, cross-stack refs, asset cache busting |
| [07](07-strands-agents-patterns.md) | Strands Agents SDK | Sequential pipeline, tool patterns, regex score extraction, OTEL tracing |

---

## Fastest Path to a Working Agent System on AWS

If you're starting fresh, do this in order:

1. **Set up CDK first** with `cdk.json` pointing `output` to `/tmp/`, `.dockerignore` in project root
2. **Deploy AuthStack first** and export `user_pool_id` and `app_client_id` as CDK cross-stack refs
3. **Use Bedrock KB, not raw AOSS** — zero idle cost, one API call to retrieve
4. **Default to `us.anthropic.claude-3-5-haiku-20241022-v1:0`** — no Marketplace opt-in needed
5. **Design API as async from the start** — async Lambda self-invoke for any pipeline > 10 seconds
6. **Test JWT auth early** — decode tokens at jwt.io before wiring up the authorizer

---

## Cost at the Hard Cap (3,100 req/month)

| Service | Cost |
|---------|------|
| Bedrock Claude 3.5 Haiku | ~$50 |
| Lambda + API Gateway | ~$0 |
| DynamoDB PAY_PER_REQUEST | < $1 |
| Bedrock Knowledge Bases | < $1 |
| Cognito (< 10K MAU) | $0 |
| S3 | ~$1 |
| **Total** | **~$52/month** |

With Claude 3 Haiku (if Marketplace opt-in done): ~$20/month.
