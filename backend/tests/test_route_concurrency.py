"""No route handler runs its work on the event loop.

The app runs as one uvicorn process. An `async def` handler runs in the
event loop's own thread, so anything blocking inside it -- a synchronous
SQLAlchemy session, a synchronous model call, reading a large upload --
stops every other request until it finishes. A plain `def` handler is run
in anyio's threadpool instead and holds one of its tokens while it works.

Measured 2026-11-18 before the fix: /health answered in 3 ms, then took
18.1 s while one 20-second baseline generation ran, answering the moment
the generation ended (`docs/roadmap.md`, "Generating a baseline froze
every request"). Thirteen routes were `async def` over a synchronous
session; all were made `def`.

This reads the routes the app actually registered, not the source, so a
route added next year is checked the day it is added. It is a structural
check, not a timing one: a timing test only sees the routes it drives.

Both allowlists are written out here, each entry with its reason. An
async handler or dependency that genuinely does no blocking work may be
added with a reason that says so; the default answer is `def`.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app

_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}
_APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Endpoints allowed to be `async def`. None today.
ASYNC_ENDPOINTS_ALLOWED: dict[str, str] = {}

# Dependencies allowed to be async, keyed "module:qualname".
ASYNC_DEPENDENCIES_ALLOWED: dict[str, str] = {
    "app.auth:get_current_user": (
        "Must run in the request's own task so set_current_actor() reaches "
        "every later threadpool dispatch; its database work is handed to "
        "run_in_threadpool (see its docstring)."
    ),
}


def _is_async(call) -> bool:
    fn = inspect.unwrap(call if inspect.isroutine(call) else type(call).__call__)
    return inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)


def _name(call) -> str:
    fn = call if inspect.isroutine(call) else type(call)
    return f"{fn.__module__}:{fn.__qualname__}"


def _registered_routes(routes) -> list[APIRoute]:
    """Every APIRoute, however this FastAPI version nests included routers.

    0.138 wraps each include in an object holding `original_router`;
    other versions flatten them or use a Mount with `.routes`.
    """
    found: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            found.append(r)
        elif hasattr(r, "original_router"):
            found.extend(_registered_routes(r.original_router.routes))
        elif hasattr(r, "routes"):
            found.extend(_registered_routes(r.routes))
    return found


def _dependencies(dependant):
    for sub in dependant.dependencies:
        yield sub
        yield from _dependencies(sub)


def _decorated_route_count() -> int:
    """Route decorators in the source, read by parsing it."""
    count = 0
    for path in _APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in _HTTP_VERBS
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id in {"router", "app"}
                ):
                    count += 1
    return count


ROUTES = _registered_routes(app.routes)


def test_walker_reaches_every_route():
    assert len(ROUTES) == _decorated_route_count(), (
        "fixture must reach every registered route or the checks below "
        "prove nothing: the walker found a different number of routes "
        "than there are route decorators in app/"
    )
    assert any(r.path == "/health" for r in ROUTES), "fixture must see /health"


def test_no_route_handler_is_async():
    offenders = sorted(
        f"{sorted(r.methods)} {r.path} -> {_name(r.endpoint)}"
        for r in ROUTES
        if _is_async(r.endpoint) and _name(r.endpoint) not in ASYNC_ENDPOINTS_ALLOWED
    )
    assert not offenders, (
        "async def route handlers run on the event loop and block every "
        "other request while they work; make them def (read an upload with "
        "file.file.read()):\n" + "\n".join(offenders)
    )


def test_no_unlisted_async_dependency():
    seen: set[str] = set()
    offenders: set[str] = set()
    for r in ROUTES:
        for dep in _dependencies(r.dependant):
            if dep.call is None or not _is_async(dep.call):
                continue
            name = _name(dep.call)
            seen.add(name)
            if name not in ASYNC_DEPENDENCIES_ALLOWED:
                offenders.add(f"{name} (via {r.path})")
    assert "app.auth:get_current_user" in seen, (
        "fixture must see get_current_user, the one async dependency, or "
        "the dependency walk is not reaching anything"
    )
    assert not offenders, (
        "an async dependency runs on the event loop; make it def, or list "
        "it in ASYNC_DEPENDENCIES_ALLOWED with the reason it does no "
        "blocking work there:\n" + "\n".join(sorted(offenders))
    )


def test_allowlists_name_real_things():
    names = {_name(r.endpoint) for r in ROUTES}
    for name in ASYNC_ENDPOINTS_ALLOWED:
        assert name in names, f"stale allowlist entry: {name}"
