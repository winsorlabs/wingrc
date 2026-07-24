"""Deny-by-default regression harness (I.1).

Walks the real app's routes and asserts every one either carries an auth
dependency (get_current_user, require_org_access, or require_role) somewhere
in its resolved dependency chain, or is on the explicit allowlist below. A
new route added to any router fails this test automatically unless its
author either wires up a guard or makes a deliberate, reviewable edit to
_PUBLIC_ROUTES — that edit shows up in the diff and must be justified in the
commit message (see docs/PLAN-auth-rbac-completion.md, I.1).

No database required — this is pure route/dependency introspection against
the app object, not a live request.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute, _IncludedRouter

from app.auth import get_current_user
from app.main import app

# Literal allowlist, not a prefix match. Every entry here is a route that
# authenticates by a mechanism FastAPI's Depends graph can't see:
#   - /health: unauthenticated by design (liveness probe)
#   - /auth/login, /auth/callback: no session exists yet to check
#   - /auth/set-password, /auth/mfa/enroll(/confirm), /auth/mfa/verify: these
#     run mid-login-flow, authenticated instead by the short-lived signed
#     wingrc_mfa_pending / wingrc_auth_flow state cookies (see app/auth.py's
#     make_state_payload/verify_state_cookie), not by get_current_user
#   - /docs, /openapi.json, /redoc, /docs/oauth2-redirect: FastAPI's own
#     auto-registered documentation routes, plain Starlette Route objects
#     with no dependant tree at all
_PUBLIC_ROUTES = frozenset(
    {
        "/health",
        "/auth/login",
        "/auth/callback",
        "/auth/set-password",
        "/auth/mfa/enroll",
        "/auth/mfa/enroll/confirm",
        "/auth/mfa/verify",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/docs/oauth2-redirect",
    }
)


def _iter_routes(routes) -> Iterator:
    """Recursively unwrap FastAPI's _IncludedRouter wrapper.

    In this FastAPI version, app.include_router(...) does not flatten routes
    into app.routes as plain APIRoute objects — it stores an opaque
    _IncludedRouter wrapping the original APIRouter. Routes declared
    directly on the FastAPI app (not via include_router) appear unwrapped.
    """
    for r in routes:
        if isinstance(r, _IncludedRouter):
            yield from _iter_routes(r.original_router.routes)
        else:
            yield r


def _dependency_chain(dependant) -> Iterator:
    """Recursively walk a Dependant's sub-dependencies (router-level
    dependencies= and per-endpoint Depends() are both merged into this same
    tree by the time the route is registered)."""
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_chain(sub)


def _is_guarded(route: APIRoute) -> bool:
    for call in _dependency_chain(route.dependant):
        if call is get_current_user:
            return True
        qualname = getattr(call, "__qualname__", "")
        # require_org_access()/require_role() are closures returning an
        # inner _check function; get_current_user is itself always a
        # sub-dependency of both, so this branch is redundant with the one
        # above in practice — kept anyway so the failure is attributable to
        # a *named* guard rather than relying solely on that transitivity.
        if qualname.startswith(("require_org_access", "require_role")):
            return True
    return False


def test_every_route_is_guarded_or_allowlisted():
    unguarded = []
    for route in _iter_routes(app.routes):
        path = getattr(route, "path", None)
        if path is None or path in _PUBLIC_ROUTES:
            continue
        if not isinstance(route, APIRoute) or not _is_guarded(route):
            methods = sorted(getattr(route, "methods", None) or ())
            unguarded.append((methods, path))

    assert not unguarded, (
        "Unguarded, non-allowlisted routes found (add a guard, or a "
        f"deliberate + justified allowlist edit): {unguarded}"
    )


@pytest.mark.parametrize("path", sorted(_PUBLIC_ROUTES))
def test_allowlisted_routes_still_exist(path):
    """Catches allowlist rot: an entry for a route that was since removed
    or renamed silently loses its purpose (it never blocks anything, since
    a route that doesn't exist can't fail the guard check either)."""
    all_paths = {getattr(r, "path", None) for r in _iter_routes(app.routes)}
    assert path in all_paths, f"{path} is allowlisted but no longer a real route"


def test_deny_by_default_harness_catches_new_unguarded_route():
    """Meta-test: proves _is_guarded actually flags a real regression rather
    than passing by construction (e.g. an allowlist so broad, or a walk that
    misses routes, that nothing could ever fail). Builds a throwaway
    app/router — never touches the real app or its dependency_overrides.
    """
    scratch = FastAPI()
    scratch_router = APIRouter()

    @scratch_router.get("/scratch/unguarded")
    def _unguarded():
        return {"ok": True}

    @scratch_router.get("/scratch/guarded")
    def _guarded(_auth=Depends(get_current_user)):  # noqa: B008
        return {"ok": True}

    scratch.include_router(scratch_router)

    routes = {
        r.path: r
        for r in _iter_routes(scratch.routes)
        if isinstance(r, APIRoute)
    }
    assert not _is_guarded(routes["/scratch/unguarded"])
    assert _is_guarded(routes["/scratch/guarded"])
