#!/usr/bin/env python3
"""
Full Bedrock Knowledge Base setup with OpenSearch Serverless.

Steps:
  1. Create AOSS encryption/network/data-access policies
  2. Create AOSS collection (waits until ACTIVE)
  3. Create vector index in the collection
  4. Create Bedrock Knowledge Base pointing at the collection
  5. Create S3 data source and sync
  6. Upload sample guidelines to S3
  7. Update Lambda KNOWLEDGE_BASE_ID env var

Idempotent — skips steps already complete.

Usage:
  cd authagent/
  python3 scripts/setup_knowledge_base.py
"""

import json
import os
import time

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_sts = boto3.client("sts", region_name=AWS_REGION)
ACCOUNT_ID = _sts.get_caller_identity()["Account"]

BUCKET_NAME = f"authagent-guidelines-{ACCOUNT_ID}"
COLLECTION_NAME = "authagent-kb"
KB_NAME = "authagent-clinical-guidelines"
INDEX_NAME = "authagent-guidelines-index"

_bedrock_agent = boto3.client("bedrock-agent", region_name=AWS_REGION)
_aoss = boto3.client("opensearchserverless", region_name=AWS_REGION)
_iam = boto3.client("iam", region_name=AWS_REGION)
_s3 = boto3.client("s3", region_name=AWS_REGION)
_lambda_client = boto3.client("lambda", region_name=AWS_REGION)


SAMPLE_GUIDELINES = [
    {
        "key": "guidelines/LCD-L35539-colonoscopy.txt",
        "content": """LCD L35539: Colonoscopy - Local Coverage Determination

COVERAGE INDICATIONS (CPT: 45378, 45380, 45381, 45385):
1. Screening: Age >= 45 average risk (ICD-10: Z12.11), age >= 40 high risk with family history
2. Diagnostic: K92.1 melena, K57.30 diverticulosis, K63.5 colon polyp, K57.10 diverticulitis
3. Surveillance: Prior adenomatous polyps (1-3 year intervals)

DOCUMENTATION REQUIRED:
- Clinical indication in chart
- Prior colonoscopy results (if applicable)
- Family history for high-risk patients

MEDICAL NECESSITY SCORING:
Score >= 75 when: age criteria met, ICD-10 documented, clinical indication recorded
Score < 75 when: no clinical indication, age not documented, missing records
""",
    },
    {
        "key": "guidelines/NCD-20.9-cardiac-catheterization.txt",
        "content": """NCD 20.9: Cardiac Catheterization - National Coverage Determination

COVERAGE CRITERIA - ALL must be documented (CPT: 93454-93461):
1. Symptoms consistent with cardiac disease (I25.10 atherosclerotic heart disease, I50.x heart failure)
2. Positive/equivocal noninvasive stress test
3. Failed medical management
4. Cardiologist referral
5. Recent ECG or echocardiogram on file

NECESSITY SCORING:
>= 75: Clear CAD symptoms + positive stress test + specialist referral documented
50-74: Equivocal findings, some criteria missing
< 75: Insufficient documentation, no specialist referral, symptoms not established
""",
    },
    {
        "key": "guidelines/CPT-27447-total-knee-arthroplasty.txt",
        "content": """CPT 27447: Total Knee Arthroplasty - Coverage Criteria

ALL criteria must be documented:
1. Diagnosis: M17.11/M17.12 primary osteoarthritis, Grade 3-4 Kellgren-Lawrence on X-ray
2. Functional impairment: VAS pain >= 7/10 on weight-bearing, ADL limitations
3. Conservative treatment FAILURE (all required):
   - Physical therapy >= 6 weeks with documented response
   - NSAIDs/analgesics >= 3 months
   - Intra-articular injections >= 2 courses
4. Orthopedic surgeon referral + anesthesia clearance
5. BMI management counseling if BMI > 35

SCORING:
90-100: All criteria met, strong documentation (PT notes, X-rays, injection records)
75-89: Most criteria met, minor gaps
50-74: Conservative treatment inadequately documented
< 50: Primary criteria not established, wrong provider type
""",
    },
]


def get_or_create_kb_role() -> str:
    """Get or create IAM role for Bedrock KB to access S3."""
    role_name = "authagent-kb-role"
    try:
        role = _iam.get_role(RoleName=role_name)
        print(f"Found existing KB role: {role['Role']['Arn']}")
        return role["Role"]["Arn"]
    except _iam.exceptions.NoSuchEntityException:
        pass

    print("Creating KB IAM role...")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{AWS_REGION}:{ACCOUNT_ID}:knowledge-base/*"},
                },
            }
        ],
    }
    response = _iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
    )
    role_arn = response["Role"]["Arn"]

    _iam.put_role_policy(
        RoleName=role_name,
        PolicyName="BedrockKBPolicy",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "S3Access",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{BUCKET_NAME}", f"arn:aws:s3:::{BUCKET_NAME}/*"],
                },
                {
                    "Sid": "BedrockEmbeddings",
                    "Effect": "Allow",
                    "Action": "bedrock:InvokeModel",
                    "Resource": f"arn:aws:bedrock:{AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0",
                },
                {
                    "Sid": "AOSSAccess",
                    "Effect": "Allow",
                    "Action": "aoss:APIAccessAll",
                    "Resource": f"arn:aws:aoss:{AWS_REGION}:{ACCOUNT_ID}:collection/*",
                },
            ],
        }),
    )
    print(f"Created KB role: {role_arn}")
    time.sleep(15)  # IAM propagation
    return role_arn


def get_or_create_aoss_collection() -> tuple[str, str]:
    """Create AOSS collection with policies. Returns (collection_id, collection_endpoint)."""
    # Check if collection exists
    existing = _aoss.list_collections(
        collectionFilters={"name": COLLECTION_NAME}
    ).get("collectionSummaries", [])
    if existing:
        c = existing[0]
        print(f"Found existing collection: {c['id']}")
        detail = _aoss.batch_get_collection(ids=[c["id"]])["collectionDetails"][0]
        return c["id"], detail.get("collectionEndpoint", "")

    # Encryption policy (required first)
    print("Creating AOSS encryption policy...")
    try:
        _aoss.create_security_policy(
            name=f"{COLLECTION_NAME}-enc",
            type="encryption",
            policy=json.dumps({
                "Rules": [{"Resource": [f"collection/{COLLECTION_NAME}"], "ResourceType": "collection"}],
                "AWSOwnedKey": True,
            }),
        )
    except _aoss.exceptions.ConflictException:
        pass

    # Network policy
    print("Creating AOSS network policy...")
    try:
        _aoss.create_security_policy(
            name=f"{COLLECTION_NAME}-net",
            type="network",
            policy=json.dumps([{
                "Rules": [
                    {"Resource": [f"collection/{COLLECTION_NAME}"], "ResourceType": "collection"},
                    {"Resource": [f"collection/{COLLECTION_NAME}"], "ResourceType": "dashboard"},
                ],
                "AllowFromPublic": True,
            }]),
        )
    except _aoss.exceptions.ConflictException:
        pass

    # Data access policy
    print("Creating AOSS data access policy...")
    caller_arn = _sts.get_caller_identity()["Arn"]
    kb_role_arn = get_or_create_kb_role()
    try:
        _aoss.create_access_policy(
            name=f"{COLLECTION_NAME}-access",
            type="data",
            policy=json.dumps([{
                "Rules": [
                    {
                        "Resource": [f"collection/{COLLECTION_NAME}"],
                        "Permission": [
                            "aoss:CreateCollectionItems",
                            "aoss:DeleteCollectionItems",
                            "aoss:UpdateCollectionItems",
                            "aoss:DescribeCollectionItems",
                        ],
                        "ResourceType": "collection",
                    },
                    {
                        "Resource": [f"index/{COLLECTION_NAME}/*"],
                        "Permission": [
                            "aoss:CreateIndex",
                            "aoss:DeleteIndex",
                            "aoss:UpdateIndex",
                            "aoss:DescribeIndex",
                            "aoss:ReadDocument",
                            "aoss:WriteDocument",
                        ],
                        "ResourceType": "index",
                    },
                ],
                "Principal": [caller_arn, kb_role_arn],
            }]),
        )
    except _aoss.exceptions.ConflictException:
        pass

    # Create collection
    print(f"Creating AOSS collection '{COLLECTION_NAME}' (type: VECTORSEARCH)...")
    response = _aoss.create_collection(
        name=COLLECTION_NAME,
        type="VECTORSEARCH",
        description="Clinical guidelines vector store for AuthAgent KB",
    )
    collection_id = response["createCollectionDetail"]["id"]

    # Wait for ACTIVE
    for i in range(60):
        detail = _aoss.batch_get_collection(ids=[collection_id])["collectionDetails"][0]
        status = detail["status"]
        print(f"  Collection status: {status} (attempt {i+1}/60)")
        if status == "ACTIVE":
            endpoint = detail["collectionEndpoint"]
            print(f"Collection ACTIVE: {endpoint}")
            return collection_id, endpoint
        if status == "FAILED":
            raise RuntimeError(f"Collection creation failed: {detail}")
        time.sleep(10)

    raise TimeoutError("Collection did not become ACTIVE within 10 minutes")


def create_vector_index(endpoint: str):
    """Create the vector index in AOSS using the OpenSearch REST API."""
    print(f"Creating vector index '{INDEX_NAME}' at {endpoint}...")

    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()

    index_body = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "bedrock-knowledge-base-default-vector": {
                    "type": "knn_vector",
                    "dimension": 1024,  # Titan Embed Text v2 default
                    "method": {"name": "hnsw", "space_type": "l2", "engine": "faiss"},
                },
                "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},
            }
        },
    }

    url = f"{endpoint}/{INDEX_NAME}"
    body = json.dumps(index_body).encode()

    req = AWSRequest(method="PUT", url=url, data=body, headers={"Content-Type": "application/json"})
    SigV4Auth(credentials, "aoss", AWS_REGION).add_auth(req)

    resp = requests.put(url, data=body, headers=dict(req.headers), timeout=30)
    if resp.status_code in (200, 201):
        print(f"Index created: {resp.json()}")
    elif resp.status_code == 400 and "already exists" in resp.text:
        print(f"Index already exists.")
    else:
        print(f"Index creation response: {resp.status_code} {resp.text}")


def get_or_create_kb(collection_id: str, role_arn: str) -> str:
    """Create Bedrock Knowledge Base. Returns kb_id."""
    existing = _bedrock_agent.list_knowledge_bases(maxResults=20)
    for kb in existing.get("knowledgeBaseSummaries", []):
        if kb["name"] == KB_NAME:
            print(f"Found existing KB: {kb['knowledgeBaseId']}")
            return kb["knowledgeBaseId"]

    collection_arn = f"arn:aws:aoss:{AWS_REGION}:{ACCOUNT_ID}:collection/{collection_id}"
    print(f"Creating Knowledge Base '{KB_NAME}'...")
    response = _bedrock_agent.create_knowledge_base(
        name=KB_NAME,
        description="LCD/NCD policies and CPT/ICD-10 coverage criteria",
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": f"arn:aws:bedrock:{AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0",
            },
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": collection_arn,
                "vectorIndexName": INDEX_NAME,
                "fieldMapping": {
                    "vectorField": "bedrock-knowledge-base-default-vector",
                    "textField": "AMAZON_BEDROCK_TEXT_CHUNK",
                    "metadataField": "AMAZON_BEDROCK_METADATA",
                },
            },
        },
    )
    kb_id = response["knowledgeBase"]["knowledgeBaseId"]
    print(f"Knowledge Base created: {kb_id}")

    # Wait for ACTIVE
    for _ in range(20):
        kb = _bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
        if kb["status"] == "ACTIVE":
            break
        print(f"  KB status: {kb['status']}")
        time.sleep(10)

    return kb_id


def get_or_create_data_source(kb_id: str) -> str:
    """Create S3 data source. Returns ds_id."""
    existing = _bedrock_agent.list_data_sources(knowledgeBaseId=kb_id, maxResults=10)
    for ds in existing.get("dataSourceSummaries", []):
        if "authagent" in ds["name"]:
            print(f"Found existing data source: {ds['dataSourceId']}")
            return ds["dataSourceId"]

    print(f"Creating data source from s3://{BUCKET_NAME}/guidelines/...")
    response = _bedrock_agent.create_data_source(
        name="authagent-guidelines-s3",
        knowledgeBaseId=kb_id,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{BUCKET_NAME}",
                "inclusionPrefixes": ["guidelines/"],
            },
        },
        vectorIngestionConfiguration={
            "chunkingConfiguration": {
                "chunkingStrategy": "FIXED_SIZE",
                "fixedSizeChunkingConfiguration": {"maxTokens": 512, "overlapPercentage": 20},
            }
        },
    )
    return response["dataSource"]["dataSourceId"]


def upload_guidelines():
    """Upload sample clinical guidelines to S3."""
    print(f"Uploading sample guidelines to s3://{BUCKET_NAME}/...")
    for g in SAMPLE_GUIDELINES:
        _s3.put_object(Bucket=BUCKET_NAME, Key=g["key"], Body=g["content"].encode(), ContentType="text/plain")
        print(f"  {g['key']}")


def sync_kb(kb_id: str, ds_id: str):
    """Run ingestion job and wait for completion."""
    print("Starting KB ingestion...")
    job = _bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]
    job_id = job["ingestionJobId"]

    for _ in range(60):
        status_r = _bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )
        j = status_r["ingestionJob"]
        print(f"  Ingestion: {j['status']} | stats: {j.get('statistics', {})}")
        if j["status"] in ("COMPLETE", "FAILED"):
            if j["status"] == "FAILED":
                raise RuntimeError(f"Ingestion failed: {j.get('failureReasons')}")
            break
        time.sleep(10)


def update_lambda(kb_id: str):
    """Update Lambda KNOWLEDGE_BASE_ID env var."""
    fns = _lambda_client.list_functions()["Functions"]
    fn_name = next((f["FunctionName"] for f in fns if "AuthAgentLambda" in f["FunctionName"]), None)
    if not fn_name:
        print(f"WARNING: Lambda not found. Set KNOWLEDGE_BASE_ID={kb_id} manually.")
        return
    config = _lambda_client.get_function_configuration(FunctionName=fn_name)
    env = config.get("Environment", {}).get("Variables", {})
    env["KNOWLEDGE_BASE_ID"] = kb_id
    _lambda_client.update_function_configuration(FunctionName=fn_name, Environment={"Variables": env})
    print(f"Updated Lambda {fn_name}: KNOWLEDGE_BASE_ID={kb_id}")


def main():
    print("=== AuthAgent Knowledge Base Setup ===\n")

    role_arn = get_or_create_kb_role()
    collection_id, endpoint = get_or_create_aoss_collection()

    if endpoint:
        time.sleep(30)  # AOSS needs stabilization after becoming ACTIVE
        create_vector_index(endpoint)
        time.sleep(10)

    kb_id = get_or_create_kb(collection_id, role_arn)
    ds_id = get_or_create_data_source(kb_id)

    upload_guidelines()
    sync_kb(kb_id, ds_id)
    update_lambda(kb_id)

    print(f"\n✅ Setup complete!")
    print(f"   Knowledge Base ID: {kb_id}")
    print(f"   Data Source ID: {ds_id}")
    print(f"   AOSS Collection: {collection_id}")


if __name__ == "__main__":
    main()
