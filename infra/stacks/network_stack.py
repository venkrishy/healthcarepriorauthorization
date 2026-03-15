"""
NetworkStack — VPC with private subnets and VPC endpoints.

VPC endpoints eliminate NAT Gateway costs for DynamoDB, S3, and Bedrock traffic.
All endpoints are Interface type except DynamoDB/S3 (Gateway type, free).
"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "AuthAgentVpc",
            max_azs=2,
            nat_gateways=1,  # 1 NAT for Lambda egress to Bedrock AgentCore
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # Gateway endpoints — free, for DynamoDB and S3
        self.vpc.add_gateway_endpoint(
            "DynamoDBEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )
        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        # Interface endpoint for Bedrock (reduces NAT costs for model inference)
        self.bedrock_endpoint = self.vpc.add_interface_endpoint(
            "BedrockEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
