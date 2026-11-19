"""Each route's body limit (app/upload_limits.py) and nginx's outer bound.

On live, until 2026-11-19, every upload but one was refused above 1 MB by
nginx's default, because the one setting was scoped to from-documents
(docs/roadmap.md, "On live, an evidence file over 1 MB is refused").
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI, File, UploadFile
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import upload_limits
from app.main import app
from app.upload_limits import DEFAULT_LIMIT, MB, UPLOAD_ROUTES, BodyLimitMiddleware

CONF = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "nginx.conf"


def _routes(routes) -> list[APIRoute]:
    found: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            found.append(r)
        elif hasattr(r, "original_router"):
            found.extend(_routes(r.original_router.routes))
        elif hasattr(r, "routes"):
            found.extend(_routes(r.routes))
    return found


def _takes_a_file(route: APIRoute) -> bool:
    return any("UploadFile" in repr(p.field_info.annotation) for p in route.dependant.body_params)


ROUTES = _routes(app.routes)


def test_every_route_taking_a_file_has_a_limit_and_no_other_does():
    uploads = {(m, r.path) for r in ROUTES if _takes_a_file(r) for m in r.methods}
    assert ("POST", "/orgs/{org_id}/logo") in uploads, (
        "fixture must recognise an UploadFile parameter or this test proves nothing"
    )
    assert uploads == set(UPLOAD_ROUTES), (
        "a route that takes a file needs its limit in upload_limits.UPLOAD_ROUTES, "
        "or it is held to 1 MB; a key there that takes no file names nothing"
    )


# Written out, not read from UPLOAD_ROUTES: megabytes, the handler's cap
# plus the one-megabyte multipart allowance.
EXPECTED_MB = {
    "/orgs/{org_id}/logo": 11,
    "/orgs/{org_id}/system-description/network-diagram": 11,
    "/orgs/{org_id}/system-description/data-flow-diagram": 11,
    "/orgs/{org_id}/imports/workbook/dry-run": 21,
    "/orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence": 51,
    "/orgs/{org_id}/assessments/{assessment_id}/evidence-tasks/{task_id}/collect": 51,
    "/orgs/{org_id}/assessments/{assessment_id}/ssp-imports": 21,
    "/admin/products/import/dry-run": 6,
    "/admin/products/import/apply": 6,
    "/admin/products/import/from-documents": 101,
    "/admin/products/{product_id}/import/from-documents-with-research": 101,
    "/admin/products/{product_id}/documents": 51,
}


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)


@pytest.mark.parametrize("path", sorted(EXPECTED_MB))
def test_each_upload_route_gets_its_limit_on_a_real_path(path):
    mw = BodyLimitMiddleware(app=None)
    assert mw.limit_for("POST", _concrete(path)) == EXPECTED_MB[path] * MB


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/auth/login"),
        ("PATCH", "/orgs/{org_id}/logo"),
        ("POST", "/orgs/{org_id}/logo/extra"),
        ("POST", "/orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}"),
    ],
)
def test_everything_else_keeps_one_megabyte(method, path):
    assert BodyLimitMiddleware(app=None).limit_for(method, _concrete(path)) == DEFAULT_LIMIT
    assert DEFAULT_LIMIT == 1 * MB


@pytest.fixture
def small_app():
    inner = FastAPI()
    reads: list[int] = []

    @inner.post("/up")
    def up(file: UploadFile = File(...)) -> dict:  # noqa: B008 -- FastAPI's form
        data = file.file.read()
        reads.append(len(data))
        return {"size": len(data)}

    @inner.post("/json")
    def js(body: dict) -> dict:
        reads.append(len(body))
        return {"ok": True}

    wrapped = BodyLimitMiddleware(inner, routes=[(("POST", "/up"), 1000)])
    return TestClient(wrapped), reads


def test_under_the_limit_reaches_the_handler(small_app):
    client, reads = small_app
    r = client.post("/up", files={"file": ("a.bin", b"x" * 500)})
    assert r.status_code == 200, r.text
    assert reads == [500]


def test_over_the_limit_by_length_is_refused_with_a_sentence_unread(small_app):
    client, reads = small_app
    r = client.post("/up", files={"file": ("a.bin", b"x" * 5000)})
    assert r.status_code == 413
    assert r.json()["detail"].startswith("The upload is larger than this route accepts")
    assert reads == []


def test_over_the_limit_with_no_length_is_refused_while_read(small_app):
    client, reads = small_app

    def chunks():
        yield (b"--B\r\nContent-Disposition: form-data; name=\"file\"; "
               b"filename=\"a.bin\"\r\n\r\n" + b"x" * 600)
        yield b"x" * 600 + b"\r\n--B--\r\n"

    r = client.post("/up", content=chunks(),
                    headers={"content-type": "multipart/form-data; boundary=B"})
    assert "content-length" not in {k.lower() for k in r.request.headers}, (
        "fixture must send no Content-Length or this tests the length check again"
    )
    assert r.status_code == 413, r.text
    assert reads == []


def test_a_json_route_is_held_to_one_megabyte(small_app):
    client, reads = small_app
    r = client.post("/json", json={"a": "x" * (DEFAULT_LIMIT + 10)})
    assert r.status_code == 413
    assert reads == []


def test_the_real_app_refuses_before_auth():
    """The middleware is installed: an oversized JSON body to a route that
    needs a session is refused for size, not for the missing session."""
    r = TestClient(app).post("/auth/login", content=b"x" * (DEFAULT_LIMIT + 1),
                             headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text


def _server_block(text: str, name: str) -> str:
    for m in re.finditer(r"\bserver\s*\{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        block = text[m.start():i]
        if re.search(rf"server_name\s+{re.escape(name)}\s*;", block) and "listen 443" in block:
            return block
    raise AssertionError(f"fixture must find the {name} TLS server block")


def _size(value: str) -> int:
    n, unit = re.fullmatch(r"(\d+)([kKmMgG]?)", value).groups()
    return int(n) * {"": 1, "k": 1024, "m": MB, "g": 1024 * MB}[unit.lower()]


def test_nginx_bound_is_the_backends_largest_and_nothing_narrows_it():
    if not CONF.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    text = "\n".join(
        line.split("#", 1)[0] for line in CONF.read_text(encoding="utf-8").splitlines()
    )
    block = _server_block(text, "dev.wingrc.us")
    found = re.findall(r"client_max_body_size\s+(\S+);", block)
    assert len(found) == 1, (
        f"one client_max_body_size, at server level, for every location; found {found}"
    )
    outside = re.sub(r"\blocation\b[^{]*\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", block)
    assert "client_max_body_size" in outside, "it must be set for the server, not a location"
    # Equal, not merely at least: above the largest route, nginx only lets
    # through bodies the backend refuses.
    assert _size(found[0]) == upload_limits.LARGEST
