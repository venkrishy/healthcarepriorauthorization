# Lesson: API Gateway 29-Second Timeout — Async Lambda Worker Pattern

## The Problem

API Gateway HTTP API has a hard **29-second integration timeout**. Multi-agent pipelines
(4 agents × ~10 seconds each) exceed this. The Lambda timed out and returned 503.

---

## The Pattern: Return Immediately, Process Asynchronously

```
Client → POST /authorize → Lambda (API handler)
                                 │
                                 ├─ write PENDING to DynamoDB
                                 ├─ invoke same Lambda with Event (async, no wait)
                                 └─ return 202 { request_id }

                           Lambda (background worker)
                                 │
                                 ├─ run 4-agent pipeline (~45s)
                                 └─ write APPROVED/PENDED/DENIED to DynamoDB

Client → GET /status/{id}  → polls DynamoDB until terminal state
Client → GET /decision/{id} → reads full decision record
```

---

## Implementation

### 1. Lambda handler dispatch (main.py)

```python
def handler(event, context):
    if event.get("_worker"):
        # Background: run the full agent pipeline
        request = PriorAuthRequest(**event["request"])
        graph = build_auth_graph()
        graph.run(request, request_id=event["request_id"])
        return {"status": "done"}
    # Foreground: API Gateway path
    return _mangum(event, context)
```

### 2. Async self-invocation in /authorize

```python
_lambda_client.invoke(
    FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
    InvocationType="Event",      # fire-and-forget
    Payload=json.dumps({
        "_worker": True,
        "request_id": request_id,
        "request": request.model_dump(),
    }),
)
```

### 3. Lambda timeout must accommodate the worker path

Set the Lambda timeout to cover the full pipeline, not the API path:

```python
# CDK:
timeout=cdk.Duration.seconds(300)  # 5 minutes for worker; API path completes in < 1s
```

### 4. IAM: Lambda needs permission to invoke itself

Avoid a CDK circular dependency by using a wildcard ARN:

```python
lambda_role.add_to_policy(iam.PolicyStatement(
    actions=["lambda:InvokeFunction"],
    resources=[f"arn:aws:lambda:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:function:MyStack-*"],
))
```

Do NOT do `self.lambda_fn.function_arn` — that creates a circular CDK dependency
(Role → Policy → Lambda ARN → Lambda → Role).

---

## Alternatives

| Option | Tradeoff |
|--------|----------|
| Lambda self-invoke (this pattern) | Simple, no new infra, works for < 15 min pipelines |
| SQS + separate consumer Lambda | Better retry/DLQ handling, more moving parts |
| Step Functions | Best for complex branching/retries, costs more |
| AgentCore Runtime | Truly serverless agent host, but adds deployment complexity |

For portfolio / low-volume, Lambda self-invoke is the right trade-off.

---

## Gotcha: `AWS_LAMBDA_FUNCTION_NAME` is Auto-Injected

You do not need to set this env var in CDK. AWS Lambda automatically injects it at runtime.
Use it in `authorize.py`:

```python
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
```

If empty (local dev), fall through to the inline synchronous path.
