"""
Forwards the SMSGate phone app's traffic (/api/mobile/...) to the bundled SMSGate
server, so the phone needs only this gateway's public address - no second
hostname, port or tunnel rule.

Only the phone-facing API is forwarded. The server's send API stays on the
internal network; the gateway is the only thing that calls it.

Responses are streamed, never buffered: the app can hold a long-lived event
stream open here to be told about new messages.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.services import smsgate_server

router = APIRouter()

# Connection-level headers describe one hop and must not be copied to the next.
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
               "transfer-encoding", "upgrade", "host", "content-length"}
# uvicorn stamps its own on the way out.
_RESPONSE_SKIP = _HOP_BY_HOP | {"date", "server"}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # No read timeout: an event stream is silent between keep-alives.
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None), follow_redirects=False)
    return _client


@router.api_route(smsgate_server.MOBILE_PATH + "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def smsgate_mobile(request: Request, path: str):
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    if request.client:
        prior = request.headers.get("x-forwarded-for")
        headers["x-forwarded-for"] = f"{prior}, {request.client.host}" if prior else request.client.host
    headers.setdefault("x-forwarded-proto", request.url.scheme)
    headers.setdefault("x-forwarded-host", request.headers.get("host", ""))

    url = f"{smsgate_server.internal_url()}{smsgate_server.MOBILE_PATH}/{path}"
    client = _get_client()
    upstream_req = client.build_request(
        request.method, url, params=request.query_params, headers=headers, content=await request.body(),
    )
    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.HTTPError:
        return JSONResponse(status_code=503, content={"message": "The SMSGate server isn't running on this gateway."})

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _RESPONSE_SKIP}
    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code, headers=out_headers,
        background=BackgroundTask(upstream.aclose),
    )
