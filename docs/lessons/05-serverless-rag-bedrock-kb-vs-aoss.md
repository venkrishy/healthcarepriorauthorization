# Lesson: Serverless RAG — Bedrock Knowledge Bases vs OpenSearch Serverless

## The Problem

The original plan used OpenSearch Serverless (AOSS) for vector search. AOSS is billed in
OCU-hours with a minimum floor that makes it non-serverless in practice.

---

## OpenSearch Serverless Cost Reality

| Fact | Detail |
|------|--------|
| Minimum OCUs | 2 OCUs always running (1 ingest + 1 search) |
| Cost | ~$0.24/OCU-hr × 2 × 730 hrs = **~$350/month** |
| Scale to zero? | No. OCUs run 24/7 regardless of traffic |
| Vector search | Cannot share OCUs with other collection types |

For a portfolio project or low-volume service, $350/month idle cost is unacceptable.

---

## Bedrock Knowledge Bases — Truly Serverless RAG

Bedrock Knowledge Bases wraps AOSS underneath but bills per **retrieval query** only.

| Fact | Detail |
|------|--------|
| Cost per query | $0.00000004 (essentially free) |
| Idle cost | $0 |
| Embeddings | Titan Embeddings v2 managed automatically |
| Data source | S3 bucket — upload PDFs/text, trigger sync |
| Query API | `bedrock-agent-runtime:Retrieve` |

At 3,100 requests/month × 4 guideline lookups = 12,400 queries = **< $0.01/month**.

---

## AOSS Data Access Policy Timing Issue

If you do use AOSS directly (for cost-optimized high-volume workloads), be aware:

**Data access policies take 60-120 seconds to propagate after creation.** Creating an
index or writing data immediately after creating the policy will get 403 errors even though
the policy exists. Add an explicit wait:

```python
import time
print("Waiting for data access policy to propagate...")
time.sleep(120)
# Now safe to create index
```

---

## Bedrock KB Limitation: Cannot Create in CDK Without Pre-Existing AOSS

If you try to create a `CfnKnowledgeBase` in CDK pointing at a `CfnCollection` in the same
stack, CloudFormation will 403 on the KB creation because the AOSS collection isn't fully
initialized yet (data access policies still propagating).

**Solution:** Create the KB separately from CDK, via a setup script run after the AOSS
collection is ACTIVE:

```
1. cdk deploy  → creates AOSS collection (status: ACTIVE after ~2min)
2. python scripts/setup_knowledge_base.py  → creates vector index, KB, data source
3. export KNOWLEDGE_BASE_ID=<id>
4. cdk deploy  → Lambda picks up the KB ID via env var
```

---

## Recommendation

For new projects with < 100K RAG queries/month:
- Use **Bedrock Knowledge Bases** — zero idle cost, no ops, native Strands integration
- Skip raw AOSS unless you need millisecond P99 latency at high sustained throughput

For S3 fallback (dev without KB):
```python
KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
if not KNOWLEDGE_BASE_ID:
    return _s3_keyword_search(query, ...)  # keyword match on guideline text files
```
