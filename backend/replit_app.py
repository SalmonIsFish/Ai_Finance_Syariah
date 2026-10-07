"""Production entrypoint (`uvicorn replit_app:app`): local_api.app, with GET /
redirected to the dashboard.

The name is historical -- this began as a Replit deploy shim and the VPS service
still starts it by this name (docs/deployment/VPS_RUNBOOK.md), so it is kept.

The dashboard itself is the React app in dashboard-v2/dist, served by
local_api.py's own owner-authenticated /dashboard router. This module used to
register a second /dashboard router for the legacy single-file
dashboard/index.html. It was dead code: local_api registers its router first,
and Starlette answers with the first matching route, so the legacy page could
not be reached. That page has been retired.

Root redirect: local_api.py's own GET / (the JSON route listing) is untouched in
local dev and in the test suite, which use local_api.app directly and never
import this module. Since `app` here is the same object as local_api.app, not a
copy, importing this module removes that root route and adds a redirect in its
place -- so a visitor to the bare domain lands on the dashboard, not on JSON.
"""

from fastapi.responses import RedirectResponse

from local_api import app

app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/"]


@app.get("/")
def root_redirects_to_dashboard() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/")
