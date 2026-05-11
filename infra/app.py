#!/usr/bin/env python3
"""
AuthAgent CDK App — deploys all infrastructure stacks.

Usage:
  cd authagent/infra
  cdk deploy --all --require-approval never

Stack order (respects dependencies):
  1. NetworkStack   — VPC + subnets + VPC endpoints
  2. AuthStack      — Cognito User Pool + App Client
  3. DataStack      — DynamoDB + Bedrock Knowledge Base + S3
  4. AgentCoreStack — S3 deployment bucket + IAM role
  5. ApiStack       — Lambda + API Gateway (depends on all above)
"""

import os

import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.auth_stack import AuthStack
from stacks.data_stack import DataStack
from stacks.agentcore_stack import AgentCoreStack
from stacks.api_stack import ApiStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

network_stack = NetworkStack(app, "AuthAgentNetwork", env=env)
auth_stack = AuthStack(app, "AuthAgentAuth", env=env)
data_stack = DataStack(app, "AuthAgentData", env=env)
agentcore_stack = AgentCoreStack(app, "AuthAgentCore", env=env)

# ApiStack depends on outputs from all other stacks
api_stack = ApiStack(
    app,
    "AuthAgentApi",
    user_pool=auth_stack.user_pool,
    app_client_id=auth_stack.app_client.user_pool_client_id,  # cross-stack ref, no env var needed
    decisions_table=data_stack.decisions_table,
    audit_table=data_stack.audit_table,
    rate_limits_table=data_stack.rate_limits_table,
    guidelines_bucket=data_stack.guidelines_bucket,
    knowledge_base_id=os.environ.get("KNOWLEDGE_BASE_ID", "PLACEHOLDER"),
    agentcore_runtime_arn=os.environ.get("AGENTCORE_RUNTIME_ARN", ""),
    env=env,
)

# Explicit dependency declarations
api_stack.add_dependency(network_stack)
api_stack.add_dependency(auth_stack)
api_stack.add_dependency(data_stack)
api_stack.add_dependency(agentcore_stack)

# Tags applied to all resources for cost allocation
for stack in [network_stack, auth_stack, data_stack, agentcore_stack, api_stack]:
    cdk.Tags.of(stack).add("Project", "authagent")
    cdk.Tags.of(stack).add("Environment", os.environ.get("ENVIRONMENT", "dev"))
    cdk.Tags.of(stack).add("ManagedBy", "cdk")

app.synth()
