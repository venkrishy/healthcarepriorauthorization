"""
AuthAgent FastAPI application.

Entry point for both local development (uvicorn) and AWS Lambda (via Mangum adapter).
OpenTelemetry auto-instrumentation is enabled when launched via:
  opentelemetry-instrument python -m uvicorn authagent.api.main:app
Or in AgentCore:
  entryPoint: ['opentelemetry-instrument', 'main.py']
"""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from authagent.api.routers import authorize, status, decision, audit
from authagent.tools.rate_limiter import get_current_usage

app = FastAPI(
    title="AuthAgent",
    description="AWS-native healthcare prior authorization system powered by Strands Agents + Bedrock",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — restrict in production via env var
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
)

# Auto-instrument FastAPI with OTEL
FastAPIInstrumentor.instrument_app(app)

# Register routers
app.include_router(authorize.router, tags=["Authorization"])
app.include_router(status.router, tags=["Status"])
app.include_router(decision.router, tags=["Decision"])
app.include_router(audit.router, tags=["Audit"])


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "authagent"}


@app.get("/metrics")
async def metrics():
    """
    Usage metrics — current daily/monthly request counts.
    Does NOT expose PHI. Safe for monitoring.
    """
    try:
        usage = get_current_usage()
        return {
            "rate_limits": usage,
            "service": "authagent",
        }
    except Exception as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tracer = trace.get_tracer("authagent.api")
    span = trace.get_current_span()
    span.record_exception(exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "message": "An unexpected error occurred"},
    )


# Mangum adapter — API Gateway events → ASGI
_mangum = Mangum(app, lifespan="off")


def handler(event, context):
    """
    Lambda entry point.

    Two call modes:
      1. API Gateway HTTP event → forwarded to FastAPI via Mangum.
      2. Background worker event ({"_worker": true, ...}) → run agent graph
         inline. Invoked asynchronously by POST /authorize so it doesn't
         block within API Gateway's 29 s integration timeout.
    """
    if event.get("_worker"):
        import json
        from authagent.models.request import PriorAuthRequest
        from authagent.graph.auth_graph import build_auth_graph
        from authagent.tools.write_decision import update_status

        request_id = event["request_id"]
        try:
            request = PriorAuthRequest(**event["request"])
            graph = build_auth_graph()
            graph.run(request, request_id=request_id)
        except Exception as exc:
            update_status(request_id, "FAILED")
            # Log but don't re-raise — async invocations have no caller to receive errors
            import traceback
            print(f"[worker] graph failed for {request_id}: {exc}")
            traceback.print_exc()
        return {"status": "done", "request_id": request_id}

    return _mangum(event, context)
