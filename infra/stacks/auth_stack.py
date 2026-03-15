"""
AuthStack — Amazon Cognito User Pool + Resource Server + App Client.

HIPAA-aligned: MFA enabled, no PHI in user attributes, custom OAuth scopes.
Scopes: authagent/submit, authagent/read, authagent/audit
"""

import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from constructs import Construct


class AuthStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.user_pool = cognito.UserPool(
            self,
            "AuthAgentUserPool",
            user_pool_name="authagent-users",
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=False, mutable=True),
            ),
            account_recovery=cognito.AccountRecovery.NONE,
            self_sign_up_enabled=False,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Define scopes once — reuse in both resource server and app client
        submit_scope = cognito.ResourceServerScope(
            scope_name="submit",
            scope_description="Submit prior authorization requests",
        )
        read_scope = cognito.ResourceServerScope(
            scope_name="read",
            scope_description="Read authorization decisions and status",
        )
        audit_scope = cognito.ResourceServerScope(
            scope_name="audit",
            scope_description="Access full audit trail",
        )

        resource_server = self.user_pool.add_resource_server(
            "AuthAgentResourceServer",
            identifier="authagent",
            scopes=[submit_scope, read_scope, audit_scope],
        )

        self.app_client = self.user_pool.add_client(
            "AuthAgentAppClient",
            user_pool_client_name="authagent-service",
            auth_flows=cognito.AuthFlow(user_password=False, user_srp=False),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[
                    cognito.OAuthScope.resource_server(resource_server, submit_scope),
                    cognito.OAuthScope.resource_server(resource_server, read_scope),
                    cognito.OAuthScope.resource_server(resource_server, audit_scope),
                ],
            ),
            generate_secret=True,
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
            access_token_validity=cdk.Duration.hours(1),
            id_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.days(1),
        )

        self.user_pool_domain = self.user_pool.add_domain(
            "AuthAgentDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"authagent-{cdk.Aws.ACCOUNT_ID}",
            ),
        )

        cdk.CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        cdk.CfnOutput(self, "AppClientId", value=self.app_client.user_pool_client_id)
        cdk.CfnOutput(
            self,
            "TokenEndpoint",
            value=f"https://{self.user_pool_domain.domain_name}.auth.{cdk.Aws.REGION}.amazoncognito.com/oauth2/token",
        )
