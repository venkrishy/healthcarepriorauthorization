# Lesson: CDK Python Gotchas

## 1. `--app is required` Error

CDK needs to find the app entry point. Create `cdk.json` in the infra directory before
running any `cdk` command:

```json
{
  "app": "python3 app.py",
  "output": "/tmp/<project>-cdk-out",
  "context": {
    "@aws-cdk/aws-apigateway:usagePlanKeyOrderInsensitiveId": true
  }
}
```

Running `cdk` from any directory other than where `cdk.json` lives requires `--app`.

---

## 2. Circular Dependency: Lambda Self-Reference in IAM Policy

```python
# BAD — creates circular: Role → Policy → Lambda.function_arn → Lambda → Role
self.lambda_fn.add_to_role_policy(
    iam.PolicyStatement(actions=["lambda:InvokeFunction"],
                        resources=[self.lambda_fn.function_arn])
)

# GOOD — wildcard scoped to account/stack prefix, no reference to Lambda resource
lambda_role.add_to_policy(
    iam.PolicyStatement(
        actions=["lambda:InvokeFunction"],
        resources=[f"arn:aws:lambda:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:function:MyStack-*"],
    )
)
```

The circular dep appears as a CloudFormation `ValidationError: Circular dependency between
resources` during changeset creation — not at synth time.

---

## 3. `point_in_time_recovery=True` is Deprecated

```python
# OLD (deprecated warning):
dynamodb.Table(..., point_in_time_recovery=True)

# NEW:
dynamodb.Table(
    ...,
    point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
        point_in_time_recovery_enabled=True
    ),
)
```

---

## 4. Cognito Scope Objects Must Be Pre-Defined

`add_resource_server()` returns a `UserPoolResourceServer` object that does NOT expose
`.scopes`. Define `OAuthScope` objects first and reuse them:

```python
submit_scope = cognito.ResourceServerScope(scope_name="submit", scope_description="Submit PA requests")
read_scope   = cognito.ResourceServerScope(scope_name="read",   scope_description="Read decisions")

resource_server = user_pool.add_resource_server("ResourceServer",
    identifier="authagent",
    scopes=[submit_scope, read_scope],
)

app_client = user_pool.add_client("AppClient",
    o_auth=cognito.OAuthSettings(
        flows=cognito.OAuthFlows(client_credentials=True),
        scopes=[
            cognito.OAuthScope.resource_server(resource_server, submit_scope),
            cognito.OAuthScope.resource_server(resource_server, read_scope),
        ],
    )
)
```

---

## 5. DynamoDB RETAIN Removal Policy Means Manual Cleanup on Rollback

CDK sets `RemovalPolicy.RETAIN` on DynamoDB tables and S3 buckets by default (correct for
production). After a stack rollback, these resources are NOT deleted — they become orphaned.

On the next deploy, CDK tries to create resources with the same logical IDs but finds the
old ones still exist → `already exists` errors.

**Fix:** If you're in dev mode and want clean deploys:

```python
# During dev only
decisions_table = dynamodb.Table(..., removal_policy=cdk.RemovalPolicy.DESTROY)
```

Or manually delete orphaned resources before redeploying:

```bash
aws dynamodb delete-table --table-name authagent-decisions
aws s3 rb s3://authagent-guidelines-123456789 --force
```

---

## 6. CDK Asset Cache Doesn't Detect File Content Changes

CDK caches Lambda assets by hash. If you change a Python file but CDK doesn't detect it
(e.g., only a comment changed inside a Docker-bundled asset), bump a file that's definitely
in the asset — like a timestamp in `__init__.py`:

```python
# authagent/__init__.py
# build: 1773534274   ← change this number to force new asset hash
```

---

## 7. Cross-Stack References Are Better Than Env Vars

Pass outputs from one stack to another as CDK constructor parameters instead of env vars:

```python
# app.py
auth = AuthStack(app, "AuthAgentAuth", ...)
api  = ApiStack(app, "AuthAgentApi",
                user_pool=auth.user_pool,
                app_client_id=auth.app_client_id,  # direct reference
                ...)
```

This eliminates the "remember to export COGNITO_APP_CLIENT_ID before cdk deploy" problem.
CDK resolves the reference at synth time, CloudFormation handles ordering.
