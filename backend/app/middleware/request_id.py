"""
Request ID middleware.

Every request gets a request_id: reused from the client's `X-Request-ID`
header if it sent one (and it looks like a reasonable id -- capped
length, no control characters, since it flows straight into logs and
error responses and an attacker-controlled header is exactly the kind of
thing that ends up in a log-injection CVE writeup), otherwise generated.
Stored in `request.state.request_id` (for route handlers) and in
`core.context.request_id_var` (for the logging filter and anything else
that doesn't have direct access to `request`), and echoed back as a
response header so a client can correlate its own logs with ours.
"""

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import request_id_var

_VALID_CLIENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_id = request.headers.get("X-Request-ID")
        request_id = client_id if client_id and _VALID_CLIENT_ID.match(client_id) else uuid.uuid4().hex

        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response
