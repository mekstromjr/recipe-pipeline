"""Request-scoped dependencies: settings, storage, and the reviewer's identity.

Identity comes from the ``X-Authentik-Username`` header set by the Traefik forward-auth
middleware — by the time a request reaches the human paths, Authentik has authenticated it.
The header is REQUIRED (401 without it): the Service is reachable in-cluster without the
ingress, and audit stamps must never be silently blank. Local testing: pass the header.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from recipe_pipeline_server.config import Settings
from recipe_pipeline_server.storage import Storage


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def current_user(x_authentik_username: Annotated[str | None, Header()] = None) -> str:
    if not x_authentik_username:
        raise HTTPException(401, "missing X-Authentik-Username (not behind forward-auth?)")
    return x_authentik_username


SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[Storage, Depends(get_storage)]
UserDep = Annotated[str, Depends(current_user)]
