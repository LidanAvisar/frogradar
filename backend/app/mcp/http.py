"""Streamable HTTP transport at /mcp on the FastAPI app, behind a bearer token (D75).

The REST API has no auth yet (known limitation), but /mcp is meant to be shared, so it requires
`Authorization: Bearer <MCP_TOKEN>` from day one. Without MCP_TOKEN the route is not mounted (the
rest of the API still starts) and an error is logged.
"""

import hmac
import logging
from typing import Any

from fastapi import FastAPI
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings

logger = logging.getLogger(__name__)
PATH = "/mcp"


class BearerGate:
    """ASGI wrapper: 401 unless the request has the exact bearer token (constant-time compare)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="frogradar-mcp"'),
                ]})  # fmt: skip
                await send({"type": "http.response.body",
                            "body": b'{"error":"missing or invalid bearer token"}'})  # fmt: skip
                return
        await self.app(scope, receive, send)


def mount_mcp(app: FastAPI, settings: Settings, server: Any | None = None) -> Any | None:
    """Add the token-gated /mcp route; returns the MCP server (its session manager must run in the
    app's lifespan) or None when MCP_TOKEN is missing."""
    if not settings.mcp_token:
        logger.error("MCP_TOKEN is not set: the MCP HTTP transport (/mcp) is NOT started")
        return None
    from mcp.server.transport_security import TransportSecuritySettings

    from app.mcp.server import build_server

    server = server or build_server()
    hosts = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
    origins = [f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts]
    sub = server.streamable_http_app(
        streamable_http_path=PATH,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
        ),
    )
    (route,) = [r for r in sub.routes if isinstance(r, Route) and r.path == PATH]
    app.router.routes.append(
        Route(PATH, endpoint=BearerGate(route.endpoint, settings.mcp_token), methods=route.methods)
    )
    return server
