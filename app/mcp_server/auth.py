"""
HTTP-level login for the MCP endpoint.

Requests to /mcp must carry the same X-User-Key and X-API-Token headers as the
HTTP API. Anything else is rejected with 401 before it reaches the MCP server, so
an AI client sees a clear authentication failure when it connects. The tools
check the caller again (see server.py) to know which account they act for.
"""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.auth import authenticate


class McpAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            ident = await run_in_threadpool(
                authenticate, headers.get("x-user-key"), headers.get("x-api-token")
            )
            if ident is None:
                resp = JSONResponse(
                    {"error": "Unauthorized. Send your X-User-Key and X-API-Token headers."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'ApiKey realm="message-gateway"'},
                )
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
