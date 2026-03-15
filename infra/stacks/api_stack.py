"""
ApiStack — Lambda + API Gateway HTTP API + Cognito JWT authorizer + Usage Plan.

Rate limits (Layer 1 — API Gateway):
  Throttle: 10 req/s burst, 2 req/s steady
  Quota: 100/day, 3,100/month

Lambda is packaged using PythonFunction (Docker bundling from project root).
"""

import os

import aws_cdk as cdk
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from constructs import Construct

# Parent of the authagent package — Lambda bundle will contain authagent/ as a subdir
# so that "from authagent.xxx import yyy" works at runtime.
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")


class ApiStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        user_pool: cognito.UserPool,
        app_client_id: str,
        decisions_table: dynamodb.Table,
        audit_table: dynamodb.Table,
        rate_limits_table: dynamodb.Table,
        guidelines_bucket: s3.Bucket,
        knowledge_base_id: str,
        agentcore_runtime_arn: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Lambda execution role
        lambda_role = iam.Role(
            self,
            "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        decisions_table.grant_read_write_data(lambda_role)
        audit_table.grant_read_write_data(lambda_role)
        rate_limits_table.grant_read_write_data(lambda_role)
        guidelines_bucket.grant_read(lambda_role)

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock-agent-runtime:Retrieve",
                ],
                resources=["*"],
            )
        )

        # Self-invoke permission for async background worker pattern.
        # Using account-scoped wildcard to avoid a circular CDK dependency
        # (Lambda ARN → Lambda resource → Role → Policy → Lambda ARN).
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:aws:lambda:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:function:AuthAgentApi-*"
                ],
            )
        )

        if agentcore_runtime_arn:
            lambda_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock-agentcore:InvokeAgentRuntime"],
                    resources=[agentcore_runtime_arn],
                )
            )

        env_vars = {
            "DECISIONS_TABLE": decisions_table.table_name,
            "AUDIT_TABLE": audit_table.table_name,
            "RATE_LIMITS_TABLE": rate_limits_table.table_name,
            "KNOWLEDGE_BASE_ID": knowledge_base_id,
            "AGENTCORE_RUNTIME_ARN": agentcore_runtime_arn,
            "BEDROCK_MODEL_ID": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
            "DAILY_REQUEST_LIMIT": "100",
            "MONTHLY_REQUEST_LIMIT": "3100",
            "RUN_INLINE": "false",
        }

        # PythonFunction bundles dependencies via Docker from the project root.
        # entry = directory containing pyproject.toml
        # index = path to handler module (relative to entry)
        self.lambda_fn = PythonFunction(
            self,
            "AuthAgentLambda",
            entry=PROJECT_ROOT,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            index="authagent/api/main.py",
            handler="handler",
            memory_size=512,
            timeout=cdk.Duration.seconds(300),  # 5 min for background worker; API GW path < 29 s
            role=lambda_role,
            environment=env_vars,
            bundling={
                "asset_excludes": [
                    "infra",
                    ".venv",
                    "__pycache__",
                    "*.pyc",
                    "tests",
                    ".git",
                    "docs",
                    "*.swp",
                ]
            },
        )

        # API Gateway HTTP API
        self.api = apigwv2.HttpApi(
            self,
            "AuthAgentApi",
            api_name="authagent",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
                allow_methods=[apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST],
                allow_origins=["*"],
                max_age=cdk.Duration.hours(1),
            ),
        )

        # Cognito JWT authorizer
        # For Cognito client_credentials tokens, API GW validates client_id against audience.
        # app_client_id is passed as a CDK cross-stack reference from AuthStack — no env var needed.
        jwt_authorizer = authorizers.HttpJwtAuthorizer(
            "CognitoAuthorizer",
            jwt_issuer=f"https://cognito-idp.{cdk.Aws.REGION}.amazonaws.com/{user_pool.user_pool_id}",
            jwt_audience=[app_client_id],
            authorizer_name="CognitoJWT",
        )

        lambda_integration = integrations.HttpLambdaIntegration(
            "LambdaIntegration",
            self.lambda_fn,
        )

        protected_routes = [
            ("/authorize", apigwv2.HttpMethod.POST),
            ("/status/{request_id}", apigwv2.HttpMethod.GET),
            ("/decision/{request_id}", apigwv2.HttpMethod.GET),
            ("/audit/{request_id}", apigwv2.HttpMethod.GET),
        ]
        for path, method in protected_routes:
            self.api.add_routes(
                path=path,
                methods=[method],
                integration=lambda_integration,
                authorizer=jwt_authorizer,
            )

        # Public endpoints
        for path in ["/health", "/metrics"]:
            self.api.add_routes(
                path=path,
                methods=[apigwv2.HttpMethod.GET],
                integration=lambda_integration,
            )

        cdk.CfnOutput(self, "ApiUrl", value=self.api.url or "")
        cdk.CfnOutput(self, "LambdaArn", value=self.lambda_fn.function_arn)
