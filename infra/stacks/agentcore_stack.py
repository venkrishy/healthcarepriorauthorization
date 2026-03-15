"""
AgentCoreStack — S3 deployment bucket + AgentCore IAM execution role.

The AgentCore Runtime itself is deployed via CLI or scripts/deploy_agentcore.py
(not via CDK, since CDK doesn't yet have native L2 constructs for AgentCore).
This stack provisions the prerequisites.
"""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class AgentCoreStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 bucket for AgentCore deployment ZIP packages
        self.deployment_bucket = s3.Bucket(
            self,
            "AgentCoreDeploymentBucket",
            bucket_name=f"authagent-agentcore-deploy-{cdk.Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireOldDeployments",
                    enabled=True,
                    noncurrent_version_expiration=cdk.Duration.days(30),
                )
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # IAM execution role for AgentCore Runtime
        # AgentCore assumes this role when executing agent code
        self.agentcore_role = iam.Role(
            self,
            "AgentCoreExecutionRole",
            role_name="authagent-agentcore-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "BedrockAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="InvokeBedrockModels",
                            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                            resources=[
                                f"arn:aws:bedrock:{cdk.Aws.REGION}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
                                f"arn:aws:bedrock:{cdk.Aws.REGION}::foundation-model/amazon.titan-embed-text-v2:0",
                            ],
                        ),
                        iam.PolicyStatement(
                            sid="RetrieveFromKnowledgeBase",
                            actions=["bedrock-agent-runtime:Retrieve"],
                            resources=["*"],
                        ),
                    ]
                ),
                "DynamoDBAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="ReadWriteDynamoDB",
                            actions=[
                                "dynamodb:GetItem",
                                "dynamodb:PutItem",
                                "dynamodb:UpdateItem",
                                "dynamodb:Query",
                                "dynamodb:BatchWriteItem",
                            ],
                            resources=[
                                f"arn:aws:dynamodb:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:table/authagent-*",
                            ],
                        )
                    ]
                ),
                "CloudWatchAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="WriteCloudWatchLogs",
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                                "xray:PutTraceSegments",
                                "xray:PutTelemetryRecords",
                            ],
                            resources=["*"],
                        )
                    ]
                ),
                "S3ReadDeployment": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="ReadDeploymentPackage",
                            actions=["s3:GetObject"],
                            resources=[
                                self.deployment_bucket.bucket_arn,
                                f"{self.deployment_bucket.bucket_arn}/*",
                            ],
                        )
                    ]
                ),
            },
        )

        cdk.CfnOutput(self, "DeploymentBucketName", value=self.deployment_bucket.bucket_name)
        cdk.CfnOutput(self, "AgentCoreRoleArn", value=self.agentcore_role.role_arn)
