#!/usr/bin/env python3
"""
Deploy the AuthAgent to Amazon Bedrock AgentCore Runtime.

Packages the application, uploads to S3, and creates/updates
the AgentCore Runtime via boto3.

Usage:
  uv run python scripts/deploy_agentcore.py \\
      --bucket authagent-agentcore-deploy-<account-id> \\
      --role-arn arn:aws:iam::<account>:role/authagent-agentcore-role \\
      [--runtime-arn arn:aws:bedrock-agentcore:...]  # update if exists

Environment variables (read from .env or shell):
  DECISIONS_TABLE, AUDIT_TABLE, RATE_LIMITS_TABLE,
  KNOWLEDGE_BASE_ID, BEDROCK_MODEL_ID
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_s3 = boto3.client("s3", region_name=AWS_REGION)
_agentcore = boto3.client("bedrock-agentcore", region_name=AWS_REGION)

PROJECT_ROOT = Path(__file__).parent.parent
PACKAGE_NAME = "authagent-runtime.zip"


def build_zip() -> bytes:
    """
    Build a deployment ZIP containing the authagent package and dependencies.

    Installs dependencies to a temp dir, then zips everything together.
    """
    import tempfile
    import shutil

    print("Building deployment package...")
    with tempfile.TemporaryDirectory() as tmpdir:
        # Install dependencies
        subprocess.run(
            [
                sys.executable, "-m", "uv", "pip", "install",
                "--target", tmpdir,
                "--requirement", str(PROJECT_ROOT / "pyproject.toml"),
                "--no-deps",  # deps already in requirements; add separately if needed
            ],
            check=True,
        )

        # Install from pyproject.toml properly
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", tmpdir, str(PROJECT_ROOT)],
            check=True,
        )

        # Create ZIP in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            tmp_path = Path(tmpdir)
            for file_path in tmp_path.rglob("*"):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(tmp_path))
                    # Exclude test files and __pycache__
                    if "__pycache__" not in arcname and ".pyc" not in arcname:
                        zf.write(file_path, arcname)

            # Add the AgentCore entry point main.py
            main_py = """
import json
import os

from authagent.graph.auth_graph import build_auth_graph
from authagent.models.request import PriorAuthRequest
from authagent.tools.write_decision import update_status


def handler(event, context):
    \"\"\"AgentCore Runtime entry point.\"\"\"
    try:
        payload = json.loads(event.get("payload", "{}"))
        request_id = payload["request_id"]
        request_data = payload["request"]

        request = PriorAuthRequest(**request_data)
        graph = build_auth_graph()
        result = graph.run(request, request_id=request_id)

        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as exc:
        request_id = payload.get("request_id", "unknown") if "payload" in locals() else "unknown"
        update_status(request_id, "FAILED")
        raise
"""
            zf.writestr("main.py", main_py.strip())

        buf.seek(0)
        return buf.read()


def upload_to_s3(bucket: str, zip_bytes: bytes) -> str:
    """Upload the deployment ZIP to S3 and return the S3 URI."""
    version = int(time.time())
    key = f"deployments/{PACKAGE_NAME.replace('.zip', '')}-{version}.zip"
    print(f"Uploading to s3://{bucket}/{key} ({len(zip_bytes) / 1024:.1f} KB)")
    _s3.put_object(Bucket=bucket, Key=key, Body=zip_bytes)
    return f"s3://{bucket}/{key}"


def create_runtime(
    s3_uri: str,
    role_arn: str,
    kb_id: str,
    decisions_table: str,
    audit_table: str,
    rate_limits_table: str,
) -> str:
    """Create a new AgentCore Runtime and return its ARN."""
    print("Creating AgentCore Runtime...")
    response = _agentcore.create_agent_runtime(
        agentRuntimeName="authagent",
        description="Healthcare prior authorization multi-agent system",
        agentRuntimeArtifact={
            "containerArtifact": {
                "containerUri": s3_uri,  # AgentCore resolves S3 ZIP to container
            }
        },
        roleArn=role_arn,
        runtimeConfiguration={
            "containerConfiguration": {
                "containerUri": s3_uri,
                "entryPoint": ["opentelemetry-instrument", "python", "main.py"],
                "environment": {
                    "KNOWLEDGE_BASE_ID": kb_id,
                    "DECISIONS_TABLE": decisions_table,
                    "AUDIT_TABLE": audit_table,
                    "RATE_LIMITS_TABLE": rate_limits_table,
                    "BEDROCK_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
                    "AWS_REGION": AWS_REGION,
                },
            }
        },
        networkConfiguration={"networkMode": "PUBLIC"},
    )
    runtime_arn = response["agentRuntimeArn"]
    print(f"AgentCore Runtime created: {runtime_arn}")
    return runtime_arn


def update_runtime(runtime_arn: str, s3_uri: str) -> None:
    """Update an existing AgentCore Runtime with a new deployment package."""
    print(f"Updating AgentCore Runtime: {runtime_arn}")
    _agentcore.update_agent_runtime(
        agentRuntimeArn=runtime_arn,
        agentRuntimeArtifact={"containerArtifact": {"containerUri": s3_uri}},
    )
    print("Update submitted. Runtime will restart automatically.")


def main():
    parser = argparse.ArgumentParser(description="Deploy AuthAgent to AgentCore Runtime")
    parser.add_argument("--bucket", required=True, help="S3 deployment bucket name")
    parser.add_argument("--role-arn", required=True, help="AgentCore execution role ARN")
    parser.add_argument("--runtime-arn", default=None, help="Existing runtime ARN (for updates)")
    parser.add_argument("--kb-id", default=os.environ.get("KNOWLEDGE_BASE_ID", ""), help="Knowledge Base ID")
    parser.add_argument("--decisions-table", default=os.environ.get("DECISIONS_TABLE", "authagent-decisions"))
    parser.add_argument("--audit-table", default=os.environ.get("AUDIT_TABLE", "authagent-audit"))
    parser.add_argument("--rate-limits-table", default=os.environ.get("RATE_LIMITS_TABLE", "authagent-rate-limits"))
    args = parser.parse_args()

    zip_bytes = build_zip()
    s3_uri = upload_to_s3(args.bucket, zip_bytes)

    if args.runtime_arn:
        update_runtime(args.runtime_arn, s3_uri)
        print(f"Deployment updated. Runtime ARN: {args.runtime_arn}")
    else:
        runtime_arn = create_runtime(
            s3_uri=s3_uri,
            role_arn=args.role_arn,
            kb_id=args.kb_id,
            decisions_table=args.decisions_table,
            audit_table=args.audit_table,
            rate_limits_table=args.rate_limits_table,
        )
        print(f"\nDeployment complete!")
        print(f"Runtime ARN: {runtime_arn}")
        print(f"\nNext: Set AGENTCORE_RUNTIME_ARN={runtime_arn} in Lambda environment")
        print("      or run: cdk deploy AuthAgentApi -c agentcore_arn={runtime_arn}")


if __name__ == "__main__":
    main()
