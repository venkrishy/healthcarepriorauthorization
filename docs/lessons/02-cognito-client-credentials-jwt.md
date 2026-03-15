# Lesson: Cognito Client Credentials Tokens and API Gateway JWT Authorizer

## The Problem

API Gateway returned 401 Unauthorized even with a valid Cognito token. The JWT authorizer
was rejecting tokens because the `audience` was configured incorrectly.

---

## Why It Fails

Cognito issues two types of machine tokens:

| Flow | Token contains |
|------|---------------|
| Authorization Code (user login) | `aud` claim = App Client ID |
| Client Credentials (M2M / service account) | NO `aud` claim — only `client_id` claim |

API Gateway's JWT authorizer validates the `aud` claim. For client credentials tokens there
is no `aud` — so the authorizer needs to validate `client_id` instead. AWS does this
automatically when you set the **audience** in the authorizer to the **App Client ID**.

## The Fix

```python
# In CDK ApiStack:
jwt_authorizer = authorizers.HttpJwtAuthorizer(
    "CognitoAuthorizer",
    jwt_issuer=f"https://cognito-idp.{cdk.Aws.REGION}.amazonaws.com/{user_pool.user_pool_id}",
    jwt_audience=[app_client_id],   # App Client ID, NOT a resource server scope
    authorizer_name="CognitoJWT",
)
```

`jwt_audience` is validated against `client_id` for client_credentials tokens. It must be
the **App Client ID**, not a scope string like `authagent/submit`.

---

## What COGNITO_APP_CLIENT_ID Is (and Isn't)

**It is public, not a secret.**

- App Client ID is an identifier, like a username. It appears in JWT headers, URLs, and CDK
  outputs. No special protection needed.
- The **App Client Secret** is the actual secret (used in `Authorization: Basic
  base64(client_id:secret)` when fetching tokens). Never log or commit that.

---

## Best Practice: Cross-Stack Reference (No Env Var Needed)

Instead of exporting the client ID and importing it via env var at deploy time, use CDK's
cross-stack reference pattern:

```python
# auth_stack.py
self.app_client_id = app_client.user_pool_client_id  # public output

# app.py
auth = AuthStack(...)
api  = ApiStack(..., app_client_id=auth.app_client_id)

# api_stack.py  __init__ signature
def __init__(self, ..., app_client_id: str, ...):
    jwt_authorizer = authorizers.HttpJwtAuthorizer(
        ...,
        jwt_audience=[app_client_id],
    )
```

CDK resolves cross-stack references at synth time. No environment variable, no drift.

---

## Debugging Cognito JWT Issues

1. Decode the token at https://jwt.io — check which claims are present.
2. For client_credentials: expect `client_id`, `scope`, `token_use: access`. No `aud`.
3. For authorization code: expect `aud` = App Client ID, `sub` = user UUID.
4. Test with `curl -H "Authorization: Bearer <token>" <api>/health` (public endpoint first).
