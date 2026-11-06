# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - Access/security controls (task 23). REUSABLE as-is.
# ============================================================================
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import os
import httpx
import uuid
import logging

logger = logging.getLogger(__name__)

# TLS verification for the outbound Graph /me call. Behind the ADP corporate
# proxy the MITM CA is not in the default trust store, which makes httpx raise
# CERTIFICATE_VERIFY_FAILED (the same reason get_aws_credentials.py uses
# verify=False). Precedence:
#   1. GRAPH_SSL_VERIFY=false  -> verification OFF (wins over any CA bundle).
#   2. GRAPH_CA_BUNDLE=<path>  -> verify against that corporate CA .pem.
#   3. otherwise               -> default system trust store.
# NOTE: a corporate CA bundle only works when the connection is actually MITM'd
# by the proxy; the public graph.microsoft.com chain is NOT issued by the
# internal CA, so in that case use GRAPH_SSL_VERIFY=false.
_GRAPH_CA_BUNDLE = os.getenv("GRAPH_CA_BUNDLE")
_GRAPH_SSL_VERIFY_ENV = os.getenv("GRAPH_SSL_VERIFY", "true").strip().lower()
_GRAPH_VERIFY_DISABLED = _GRAPH_SSL_VERIFY_ENV in ("false", "0", "no", "off")
if _GRAPH_VERIFY_DISABLED:
    GRAPH_SSL_VERIFY: object = False
elif _GRAPH_CA_BUNDLE:
    GRAPH_SSL_VERIFY = _GRAPH_CA_BUNDLE
else:
    GRAPH_SSL_VERIFY = True

if GRAPH_SSL_VERIFY is False:
    logger.warning(
        "GRAPH_SSL_VERIFY disabled - TLS verification for Graph /me is OFF "
        "(dev/proxy mode). Do not use this in production."
    )


class GraphAuthMiddleware(BaseHTTPMiddleware):

    EXCLUDED_PATHS = [
        "/docs",
        "/openapi.json",
        "/redoc",
        "/health",
        # Deliverable 1 canonical-model verification route (local introspection,
        # same open-access treatment as /health).
        "/ingest_session"
    ]

    # Deliverable 2 exact-JSON-contract verification routes: local introspection
    # of the contract layer (metadata/schema/validate/parse), same open-access
    # treatment as /ingest_session. Matched by prefix since the router mounts
    # several sub-paths under /contract.
    EXCLUDED_PREFIXES = [
        "/contract/",
        # Deliverable 3 validation-rule-identification verification routes:
        # local introspection (catalog/metadata/session/analyze), same
        # open-access treatment as /ingest_session and /contract/.
        "/validation-analysis/",
        # Deliverable 4 validation-IR / validation-JSON-generation verification
        # routes (metadata/build/generate/session), same open-access treatment.
        "/validation-ir/",
        # Deliverable 5 transformation-rule-identification verification routes
        # (catalog/metadata/session/analyze), same open-access treatment.
        "/transformation-analysis/",
        # Deliverable 6 transformation-IR / transformation-JSON-generation
        # verification routes (metadata/build/generate/session), same
        # open-access treatment.
        "/transformation-ir/",
        # Deliverable 7 complete-JSON-configuration-generation verification
        # routes (metadata/generate/assemble/session), same open-access
        # treatment as the other deliverable verification routes.
        "/complete-config/",
        # Deliverable 8 existing-API-execution / development-accuracy-loop
        # verification routes (metadata/run/session/history), same open-access
        # treatment as the other deliverable verification routes.
        "/accuracy-loop/",
        # End-to-end pipeline (generate -> process-API verify -> save)
        # verification routes (metadata/run/session), same open-access treatment
        # as the other deliverable verification routes.
        "/pipeline/",
    ]

    async def dispatch(self, request: Request, call_next):

        path = request.url.path
        logger.info("Incoming request: %s %s", request.method, path)

        # Skip authentication for excluded paths
        if path in self.EXCLUDED_PATHS:
            return await call_next(request)
        if any(path.startswith(prefix) for prefix in self.EXCLUDED_PREFIXES):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Get Authorization header
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            logger.warning("Missing Authorization header for %s", path)
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "message": "Authorization header missing"
                }
            )

        # Validate Bearer format
        if not auth_header.startswith("Bearer "):
            logger.warning("Invalid authorization format for %s", path)
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "message": "Invalid authorization format"
                }
            )

        token = auth_header.split(" ")[1]

        try:

            headers = {
                "Authorization": f"Bearer {token}"
            }

            logger.info("Calling Graph /me to validate token")
            # Increased timeout for slower networks; use connect timeout + read timeout
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=15.0),
                verify=GRAPH_SSL_VERIFY,
            ) as client:

                response = await client.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers=headers
                )

            logger.info("Graph /me response status: %s", response.status_code)

            # Token valid
            if response.status_code == 200:

                user_data = response.json()
                logger.info(
                    "Graph /me success: user_id=%s  displayName=%s  userPrincipalName=%s",
                    user_data.get("id"),
                    user_data.get("displayName"),
                    user_data.get("userPrincipalName"))
                    

                # Flat attributes expected by every endpoint in main.py
                request.state.user_id    = user_data.get("id")
                request.state.user_name  = user_data.get("displayName")
                request.state.user_email = user_data.get("userPrincipalName")
                request.state.claims     = user_data

                # session_id: use the query-param value if provided, otherwise None.
                # Endpoints that require a session_id will validate it themselves.
                request.state.session_id = request.query_params.get("session_id")

                # Legacy dict kept for any code that reads request.state.user
                request.state.user = {
                    "name":    request.state.user_name,
                    "email":   request.state.user_email,
                    "user_id": request.state.user_id,
                }

                logger.info(
                    "Auth OK — user_id=%s  session_id=%s",
                    request.state.user_id,
                    request.state.session_id,
                )

                return await call_next(request)

            # Token expired / invalid
            elif response.status_code == 401:
                logger.warning("Graph /me returned 401 — token expired or invalid")
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "message": "Token expired or invalid"
                    }
                )

            # Any other Graph issue
            else:
                logger.error(
                    "Graph /me returned unexpected status %s: %s",
                    response.status_code,
                    response.text[:300],
                )
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "message": "Authentication failed"
                    }
                )

        except httpx.TimeoutException as exc:
            logger.error("Timeout calling Graph /me: %s", exc)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "message": "Authentication service timeout. Please retry or check your network connection."
                }
            )

        except httpx.RequestError as exc:
            logger.exception("Network error calling Graph /me: %s", exc)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "message": "Unable to connect to Microsoft Graph. Please check your network connection."
                }
            )

        except Exception as e:
            logger.exception("Unexpected error in auth middleware: %s", e)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "message": str(e)
                }
            )

