#!/usr/bin/env python3
"""
Ingest clinical guidelines into Bedrock Knowledge Base.

Uploads LCD/NCD PDFs and structured CPT/ICD-10 policy documents
to the S3 guidelines bucket, then triggers a Knowledge Base sync.

Usage:
  uv run python scripts/ingest_guidelines.py --bucket authagent-guidelines-<account-id> \\
      --kb-id <knowledge-base-id> --source-dir ./sample_guidelines/

Sample guidelines directory structure:
  sample_guidelines/
  ├── LCD-L35539-colonoscopy.pdf
  ├── NCD-20.9-cardiac-cath.pdf
  └── CPT-27447-knee-replacement-criteria.txt
"""

import argparse
import json
import os
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_s3 = boto3.client("s3", region_name=AWS_REGION)
_bedrock_agent = boto3.client("bedrock-agent", region_name=AWS_REGION)


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".json"}

# Sample synthetic guidelines for demo/testing (no real PHI)
SAMPLE_GUIDELINES = [
    {
        "filename": "LCD-L35539-colonoscopy.txt",
        "content": """LCD L35539: Colonoscopy

Policy Type: LCD (Local Coverage Determination)
Effective Date: 2024-01-01

COVERAGE INDICATIONS:
Colonoscopy is covered when ONE or more of the following criteria are met:

1. Screening colonoscopy for colorectal cancer:
   - Age >= 45 for average risk individuals
   - Age >= 40 or 10 years before earliest family member diagnosis for high-risk

2. Diagnostic colonoscopy (ICD-10 required):
   - K92.1 - Melena (black stools)
   - K57.30 - Diverticulosis without mention of hemorrhage
   - K63.5 - Polyp of colon (surveillance)
   - Z12.11 - Encounter for screening for malignant neoplasm of colon

3. Surveillance colonoscopy:
   - History of adenomatous polyps: 1-3 years after initial removal
   - History of colorectal cancer: 1 year post-surgery

CPT CODES: 45378, 45380, 45381, 45382, 45384, 45385

DOCUMENTATION REQUIREMENTS:
- Clinical indication clearly documented
- Previous colonoscopy results (if applicable)
- Family history (for high-risk screening)
- Referring provider specialty

EXCLUSIONS:
- Routine annual colonoscopy without clinical indication
- Age < 45 for average-risk asymptomatic patients
""",
    },
    {
        "filename": "NCD-20.9-cardiac-catheterization.txt",
        "content": """NCD 20.9: Cardiac Catheterization

Policy Type: NCD (National Coverage Determination)
Effective Date: 2024-01-01

COVERAGE INDICATIONS:
Cardiac catheterization is covered for:

1. Evaluation of suspected or known coronary artery disease (CAD):
   - ICD-10: I25.10 - Atherosclerotic heart disease
   - Positive or equivocal noninvasive stress test
   - Class III/IV angina (CCS classification)

2. Pre-operative evaluation:
   - Valve surgery candidates
   - Congenital heart disease repair

3. Assessment of heart failure:
   - ICD-10: I50.1, I50.2, I50.3 - Various heart failure types
   - Unexplained cardiomyopathy

CPT CODES: 93454, 93455, 93456, 93457, 93458, 93459, 93460, 93461

MEDICAL NECESSITY CRITERIA:
All of the following must be documented:
a) Clinical symptoms consistent with cardiac disease
b) Failed response to medical management OR urgent/emergent presentation
c) Appropriate specialist referral (cardiologist)
d) Recent ECG or echocardiogram results

NECESSITY SCORE GUIDANCE:
- Score >= 75: Clear CAD indication with positive noninvasive testing
- Score 50-74: Equivocal findings, additional workup may be needed
- Score < 75: Insufficient documentation or indication not established
""",
    },
    {
        "filename": "CPT-27447-total-knee-arthroplasty.txt",
        "content": """CPT 27447: Total Knee Arthroplasty (TKA)

Policy Type: Coverage Criteria
Effective Date: 2024-01-01

COVERAGE CRITERIA (ALL must be met):

1. Diagnosis:
   - ICD-10: M17.11, M17.12 - Primary osteoarthritis, knee
   - ICD-10: M17.31, M17.32 - Secondary osteoarthritis, knee
   - Radiographic evidence of severe joint space narrowing (Grade 3-4 Kellgren-Lawrence)

2. Functional impairment:
   - Significant limitation in activities of daily living
   - Documented pain affecting quality of life
   - VAS pain score >= 7/10 on weight-bearing

3. Conservative treatment failure (documentation required):
   - Physical therapy: minimum 6 weeks
   - NSAIDs or analgesics: minimum 3 months
   - Intra-articular injections: minimum 2 courses
   - Weight management counseling (if BMI > 35)

4. Provider requirements:
   - Orthopedic surgeon referral
   - Pre-operative medical clearance

DOCUMENTATION REQUIRED:
- X-ray reports within 12 months
- Physical therapy notes showing treatment and response
- BMI and weight management history
- Anesthesia risk assessment

SCORING GUIDE:
- 90-100: All criteria met with strong documentation
- 75-89: Most criteria met, minor documentation gaps
- 50-74: Conservative treatment inadequately documented
- <50: Criteria not established
""",
    },
]


def upload_guidelines(bucket_name: str, source_dir: str | None = None) -> list[str]:
    """Upload guidelines to S3. Uses sample data if source_dir is None."""
    uploaded = []

    if source_dir:
        source_path = Path(source_dir)
        files = [f for f in source_path.rglob("*") if f.suffix.lower() in SUPPORTED_EXTENSIONS]
        for file_path in files:
            key = f"guidelines/{file_path.name}"
            print(f"Uploading {file_path} -> s3://{bucket_name}/{key}")
            _s3.upload_file(str(file_path), bucket_name, key)
            uploaded.append(key)
    else:
        print("No source directory provided — uploading sample synthetic guidelines...")
        for guideline in SAMPLE_GUIDELINES:
            key = f"guidelines/{guideline['filename']}"
            print(f"Uploading sample: s3://{bucket_name}/{key}")
            _s3.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=guideline["content"].encode("utf-8"),
                ContentType="text/plain",
                Metadata={
                    "policy_type": key.split("-")[0].upper() if "-" in key else "POLICY",
                    "source": "synthetic-demo",
                },
            )
            uploaded.append(key)

    print(f"Uploaded {len(uploaded)} guidelines")
    return uploaded


def sync_knowledge_base(kb_id: str, data_source_id: str) -> str:
    """Trigger a Bedrock Knowledge Base ingestion job and wait for completion."""
    print(f"Triggering KB sync: {kb_id} / {data_source_id}")
    response = _bedrock_agent.start_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
    )
    job_id = response["ingestionJob"]["ingestionJobId"]
    print(f"Ingestion job started: {job_id}")

    # Poll until complete
    while True:
        status_response = _bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )
        job = status_response["ingestionJob"]
        status = job["status"]
        print(f"  Status: {status} | Documents: {job.get('statistics', {})}")

        if status in ("COMPLETE", "FAILED"):
            break
        time.sleep(10)

    if status == "FAILED":
        raise RuntimeError(f"Ingestion job {job_id} failed: {job.get('failureReasons')}")

    print(f"Knowledge Base sync complete: {job_id}")
    return job_id


def main():
    parser = argparse.ArgumentParser(description="Ingest clinical guidelines into Bedrock KB")
    parser.add_argument("--bucket", required=True, help="S3 bucket name for guidelines")
    parser.add_argument("--kb-id", required=True, help="Bedrock Knowledge Base ID")
    parser.add_argument("--data-source-id", required=True, help="KB Data Source ID")
    parser.add_argument("--source-dir", default=None, help="Local directory of PDF/TXT guidelines")
    args = parser.parse_args()

    upload_guidelines(args.bucket, args.source_dir)
    sync_knowledge_base(args.kb_id, args.data_source_id)
    print("Ingestion complete.")


if __name__ == "__main__":
    main()
