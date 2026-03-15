"""
Bedrock Knowledge Bases retrieval tool for clinical guidelines.

Queries the knowledge base with a natural-language query and returns
relevant guideline excerpts. Billed per query — no idle cost.
"""

import json
import os
import time

import boto3
from opentelemetry import trace
from strands import tool

tracer = trace.get_tracer("authagent.tools.search_guidelines")

_bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
MAX_RESULTS = int(os.environ.get("KB_MAX_RESULTS", "5"))


def _s3_keyword_search(query: str, procedure_code: str, diagnosis_codes: str) -> str:
    """
    Fallback: keyword search over guidelines text files in S3.
    Used when KNOWLEDGE_BASE_ID is not configured (local dev / KB not yet set up).
    """
    import json
    import os
    import boto3

    bucket = os.environ.get("GUIDELINES_BUCKET", f"authagent-guidelines-{os.environ.get('AWS_ACCOUNT_ID', '')}")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    keywords = set((query + " " + procedure_code + " " + diagnosis_codes).lower().split())
    results = []

    try:
        objects = s3.list_objects_v2(Bucket=bucket, Prefix="guidelines/").get("Contents", [])
    except Exception:
        objects = []

    for obj in objects[:10]:
        try:
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode("utf-8")
            key_lower = body.lower()
            matches = sum(1 for k in keywords if k in key_lower and len(k) > 3)
            if matches > 0:
                gid = obj["Key"].split("/")[-1].replace(".txt", "").replace(".pdf", "")
                results.append({
                    "guideline_id": gid,
                    "title": gid.replace("-", " ").title(),
                    "policy_type": "LCD" if "LCD" in gid else "NCD" if "NCD" in gid else "CPT",
                    "relevance_score": round(min(matches / max(len(keywords), 1), 1.0), 4),
                    "excerpt": body[:800],
                    "source_uri": f"s3://{bucket}/{obj['Key']}",
                })
        except Exception:
            continue

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return json.dumps({
        "query": query,
        "results": results[:5],
        "total_found": len(results),
        "source": "s3_keyword_fallback",
        "duration_ms": 0,
    })


@tool
def search_guidelines(query: str, procedure_code: str = "", diagnosis_codes: str = "") -> str:
    """
    Search clinical guidelines in the Bedrock Knowledge Base.

    Retrieves relevant LCD/NCD policies, CPT/ICD-10 coverage criteria,
    and clinical necessity guidelines for the given query.

    Args:
        query: Natural language search query describing the clinical scenario.
        procedure_code: CPT code to include in the search context.
        diagnosis_codes: Comma-separated ICD-10 codes for context.

    Returns:
        JSON string with list of guideline excerpts and their metadata.
    """
    if not KNOWLEDGE_BASE_ID:
        return _s3_keyword_search(query, procedure_code, diagnosis_codes)

    enriched_query = query
    if procedure_code:
        enriched_query = f"CPT {procedure_code}: {query}"
    if diagnosis_codes:
        enriched_query = f"{enriched_query} (ICD-10: {diagnosis_codes})"

    with tracer.start_as_current_span("kb_retrieve") as span:
        span.set_attribute("kb.query", query)
        span.set_attribute("kb.procedure_code", procedure_code)
        span.set_attribute("kb.knowledge_base_id", KNOWLEDGE_BASE_ID)

        start_ms = int(time.time() * 1000)
        response = _bedrock_agent_runtime.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={"text": enriched_query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": MAX_RESULTS,
                }
            },
        )
        duration_ms = int(time.time() * 1000) - start_ms

        results = []
        for item in response.get("retrievalResults", []):
            content = item.get("content", {}).get("text", "")
            metadata = item.get("metadata", {})
            score = item.get("score", 0.0)

            results.append(
                {
                    "guideline_id": metadata.get("guideline_id", metadata.get("x-amz-bedrock-kb-source-uri", "")),
                    "title": metadata.get("title", "Clinical Guideline"),
                    "policy_type": metadata.get("policy_type", "LCD"),
                    "relevance_score": round(float(score), 4),
                    "excerpt": content[:1000],
                    "source_uri": metadata.get("x-amz-bedrock-kb-source-uri", ""),
                }
            )

        span.set_attribute("kb.results_count", len(results))
        span.set_attribute("kb.duration_ms", duration_ms)

        return json.dumps(
            {
                "query": query,
                "results": results,
                "total_found": len(results),
                "duration_ms": duration_ms,
            }
        )
