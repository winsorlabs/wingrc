"""How large a request body each route accepts, refused before it is read.

nginx holds one outer bound for every /api/ route (`client_max_body_size`
in deploy/nginx/nginx.conf, the largest entry below), and this is where
the per-route limit lives. It was the other way round until 2026-11-19:
nginx set 105m on from-documents alone, so every other upload, evidence
included, got nginx's 1 MB default and a bare 413 on live
(docs/roadmap.md, "On live, an evidence file over 1 MB is refused").

The limit is per route, not one number for the API, for two reasons:
- FastAPI reads and parses a body before any dependency runs, auth
  included. A JSON route left at 105 MB would let an anonymous client make
  the process hold and parse that much. Every route not listed here keeps
  the 1 MB that live's nginx gave it until now.
- The limit is placed per route without regex locations in nginx, whose
  Dockerfile accepts the base image's pcre2 HIGHs on the ground that the
  config uses none.

A body over its route's limit is refused by its Content-Length before a
byte of it is read, and by a running count when no length is sent, so a
handler never holds more than its limit. The handlers' own per-file checks
stay: they name the file, and run after the read.

Every route that takes an UploadFile is in UPLOAD_ROUTES, and every key
there names a registered route: tests/test_upload_limits.py holds both,
and nginx's bound against the largest entry.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import ssp_import_session
from .routers import admin_products, evidence, orgs

MB = 1024 * 1024

# A multipart body is its files plus boundaries, part headers and any form
# fields. A megabyte covers that, so a file at its handler's cap reaches the
# handler, which says which file and why.
MULTIPART_ALLOWANCE = 1 * MB

DEFAULT_LIMIT = 1 * MB

_EVIDENCE = evidence._MAX_FILE_BYTES + MULTIPART_ALLOWANCE
_TWO_DOCUMENTS = (
    admin_products._MAX_INGEST_FILES * evidence._MAX_FILE_BYTES + MULTIPART_ALLOWANCE
)

# (method, route path) -> largest body accepted, in bytes. The scope
# workbook and the baseline YAML had no cap of their own, only nginx's 1 MB;
# these are their first.
UPLOAD_ROUTES: dict[tuple[str, str], int] = {
    ("POST", "/orgs/{org_id}/logo"): orgs._IMAGE_MAX_BYTES + MULTIPART_ALLOWANCE,
    ("POST", "/orgs/{org_id}/system-description/network-diagram"):
        orgs._DIAGRAM_MAX_BYTES + MULTIPART_ALLOWANCE,
    ("POST", "/orgs/{org_id}/system-description/data-flow-diagram"):
        orgs._DIAGRAM_MAX_BYTES + MULTIPART_ALLOWANCE,
    ("POST", "/orgs/{org_id}/imports/workbook/dry-run"): 20 * MB + MULTIPART_ALLOWANCE,
    ("POST", "/orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence"):
        _EVIDENCE,
    ("POST", "/orgs/{org_id}/assessments/{assessment_id}/evidence-tasks/{task_id}/collect"):
        _EVIDENCE,
    ("POST", "/orgs/{org_id}/assessments/{assessment_id}/ssp-imports"):
        ssp_import_session.MAX_BYTES + MULTIPART_ALLOWANCE,
    ("POST", "/admin/products/import/dry-run"): 5 * MB + MULTIPART_ALLOWANCE,
    ("POST", "/admin/products/import/apply"): 5 * MB + MULTIPART_ALLOWANCE,
    ("POST", "/admin/products/import/from-documents"): _TWO_DOCUMENTS,
    ("POST", "/admin/products/{product_id}/import/from-documents-with-research"):
        _TWO_DOCUMENTS,
    ("POST", "/admin/products/{product_id}/documents"): _EVIDENCE,
}

LARGEST = max(UPLOAD_ROUTES.values())


def _megabytes(n: int) -> str:
    return f"{n / MB:.0f} MB"


def too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"The upload is larger than this route accepts ({_megabytes(limit)}).",
    )


def _path_regex(path: str) -> re.Pattern[str]:
    """The route's own matching rule: each {param} is one path segment,
    as Starlette compiles it for a parameter with no converter."""
    parts = re.split(r"(\{[^}]+\})", path)
    return re.compile(
        "^" + "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts) + "$"
    )


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, routes: Iterable[tuple[tuple[str, str], int]] | None = None):
        self.app = app
        items = UPLOAD_ROUTES.items() if routes is None else routes
        self._routes = [(m, _path_regex(p), limit) for (m, p), limit in items]

    def limit_for(self, method: str, path: str) -> int:
        for m, rx, limit in self._routes:
            if m == method and rx.match(path):
                return limit
        return DEFAULT_LIMIT

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope["method"], scope["path"])

        length = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    length = None
                break
        if length is not None and length > limit:
            await _send_413(send, limit)
            return

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # Raised from inside the body read, so FastAPI's
                    # handler passes it on as the 413 it is.
                    raise too_large(limit)
            return message

        await self.app(scope, counted, send)


async def _send_413(send: Send, limit: int) -> None:
    body = json.dumps({"detail": too_large(limit).detail}).encode()
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
