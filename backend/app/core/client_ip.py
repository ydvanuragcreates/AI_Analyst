"""Identify the caller for rate limits and per-visitor daily limits.

`request.client.host` is NOT safe for this behind a proxy when uvicorn is
told to trust every forwarded header (`--forwarded-allow-ips='*'`): it
then takes the LEFTMOST X-Forwarded-For entry, which the caller writes
themselves -- a different fake value per request defeats every limit.

Instead, in order:
  1. Settings.client_ip_header (e.g. "true-client-ip"): a header the
     hosting platform's edge OVERWRITES with the real client IP.
  2. Settings.trusted_proxy_hops = N > 0: the Nth entry from the RIGHT of
     X-Forwarded-For -- entries appended by our own N proxies, which a
     caller can't control (they can only prepend).
  3. The direct TCP peer (fine when nothing sits in front, e.g. local dev).

Stored values are hashed (client_key), never the raw IP.
"""

import hashlib

from fastapi import Request

from app.core.config import Settings


def client_ip(request: Request, settings: Settings) -> str:
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header, "").split(",")[0].strip()
        if value:
            return value

    if settings.trusted_proxy_hops > 0:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if len(hops) >= settings.trusted_proxy_hops:
            return hops[-settings.trusted_proxy_hops]

    return request.client.host if request.client else "unknown"


def client_key(request: Request, settings: Settings) -> str:
    """Stable, non-reversible-at-a-glance key for one caller (sha256 of the IP)."""

    return hashlib.sha256(client_ip(request, settings).encode()).hexdigest()[:32]
