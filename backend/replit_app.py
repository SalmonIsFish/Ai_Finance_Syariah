"""Replit deploy entrypoint: one process, one public port, serving both the
FastAPI API (local_api.app) and the static dashboard together.

Owned by Terminal 1 (frontend/hosting), not local_api.py, per CLAUDE.md's
boundary -- Terminal 2 owns that file exclusively. This module only imports
the existing `app` object, mounts a StaticFiles directory onto it, and
replaces the root route with a redirect to the dashboard; it adds no routes
that could collide with anything local_api.py defines, and it makes no gate,
screening, or execution decisions of its own.

Replit's free tier exposes exactly one public port, which is why the API and
the dashboard -- normally two separate local processes (see CLAUDE.md
"Running it") -- need to be combined here. Local development is unaffected:
`backend/run_local.ps1` / the uvicorn command in CLAUDE.md still start the API
alone, and the dashboard is still opened as a plain file or via its own static
server, exactly as before.

The dashboard's own apiBase default already special-cases this: when
dashboard/index.html is not opened on localhost/127.0.0.1, it defaults the API
base field to same-origin (window.location.origin), so visiting the deployed
URL needs no manual configuration.

Root redirect: local_api.py's own GET / stays exactly as it is (a judge or a
human should still be able to see the JSON route listing by asking for it
explicitly, and test_local_api_smoke.py exercises local_api.app directly,
never this module) -- but a judge pasting the bare Replit domain should land
on the dashboard, not that JSON. Since `app` here is the same object as
local_api.app, not a copy, this module removes local_api's root route and
adds a redirect in its place. That mutation only ever runs when this module
is actually imported as the deploy entrypoint (`uvicorn replit_app:app`);
local dev and the test suite never import replit_app, so local_api.app's own
GET / is untouched in both of those contexts.
"""

from pathlib import Path
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse, FileResponse
import auth

from local_api import app, get_owner_actor

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

dashboard_router = APIRouter(dependencies=[Depends(get_owner_actor)])

@dashboard_router.get("/dashboard/")
def get_dashboard_index():
    return FileResponse(DASHBOARD_DIR / "index.html")

@dashboard_router.get("/dashboard/{path:path}")
def get_dashboard_file(path: str):
    file_path = DASHBOARD_DIR / path
    if file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(DASHBOARD_DIR / "index.html")

app.include_router(dashboard_router)

app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/"]

@app.get("/")
def root_redirects_to_dashboard() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/")
