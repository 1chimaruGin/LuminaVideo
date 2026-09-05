"""FastAPI application.

The Experience plane talks only to this. Keep it thin: routers validate, delegate to a
service in state/ or execution/, and return. No business logic in a route handler.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lumina.api.routers import (
    assets,
    auth,
    channels,
    credits,
    events,
    health,
    jobs,
    projects,
    renders,
    scenes,
    script,
    uploads,
)
from lumina.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Lumina API",
        version="0.1.0",
        lifespan=lifespan,
        # Stable operation ids keep the generated TS client's method names stable.
        generate_unique_id_function=lambda route: (
            f"{route.tags[0]}_{route.name}" if route.tags else route.name
        ),
    )

    # Development accepts any origin; production accepts an explicit allowlist and nothing
    # else.
    #
    # "Any origin" and not a loopback pattern. The pattern was `localhost|127.0.0.1`, which is
    # wrong the moment the browser is not on the same machine as the server — under WSL, from
    # a phone on the same wifi, or through any tunnel. The preflight then 400s with no
    # `access-control-allow-origin`, the browser blocks the request, and `fetch` rejects with
    # no response body at all: the app cannot tell a refused origin from an unplugged cable,
    # and shows "check your connection" for a server that answered fine.
    #
    # This is safe *because* it is gated on the environment. `allow_credentials` is off in
    # development for the same reason it must be: a wildcard origin with credentials is the
    # combination browsers refuse, and the app carries a bearer token rather than a cookie.
    if settings.is_production:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=".*",
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    for router in (
        health,
        auth,
        channels,
        uploads,
        assets,
        projects,
        scenes,
        script,
        renders,
        jobs,
        credits,
        events,
    ):
        app.include_router(router.router)

    return app


app = create_app()
