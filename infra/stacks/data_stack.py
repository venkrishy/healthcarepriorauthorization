"""
DataStack — DynamoDB tables + S3 guidelines bucket.

Tables (PAY_PER_REQUEST — zero cost at zero traffic):
  authagent-decisions   — final decision per request_id
  authagent-audit       — per-agent-turn audit entries
  authagent-rate-limits — daily/monthly counters with TTL

S3:
  authagent-guidelines-<account> — LCD/NCD PDFs for Knowledge Base ingestion

Bedrock Knowledge Base must be set up manually after CDK deployment via the AWS
console or CLI. Set the KNOWLEDGE_BASE_ID environment variable on the Lambda once created.
"""

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_s3 as s3
from constructs import Construct


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 bucket for clinical guidelines (LCD/NCD PDFs)
        self.guidelines_bucket = s3.Bucket(
            self,
            "GuidelinesBucket",
            bucket_name=f"authagent-guidelines-{cdk.Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # DynamoDB: decisions table
        self.decisions_table = dynamodb.Table(
            self,
            "DecisionsTable",
            table_name="authagent-decisions",
            partition_key=dynamodb.Attribute(
                name="request_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )

        # DynamoDB: audit table
        self.audit_table = dynamodb.Table(
            self,
            "AuditTable",
            table_name="authagent-audit",
            partition_key=dynamodb.Attribute(
                name="request_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # DynamoDB: rate limits table (TTL enabled for auto-expiry)
        self.rate_limits_table = dynamodb.Table(
            self,
            "RateLimitsTable",
            table_name="authagent-rate-limits",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        cdk.CfnOutput(self, "DecisionsTableName", value=self.decisions_table.table_name)
        cdk.CfnOutput(self, "AuditTableName", value=self.audit_table.table_name)
        cdk.CfnOutput(self, "RateLimitsTableName", value=self.rate_limits_table.table_name)
        cdk.CfnOutput(self, "GuidelinesBucketName", value=self.guidelines_bucket.bucket_name)
        cdk.CfnOutput(
            self,
            "NextStep",
            value="Create the Bedrock Knowledge Base manually via the AWS console/CLI, then set KNOWLEDGE_BASE_ID on the Lambda.",
        )
