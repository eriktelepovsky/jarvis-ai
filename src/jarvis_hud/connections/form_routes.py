"""Pages and actions for form-based connections (API tokens, URLs) declared by plugins."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_hud.connections import store
from jarvis_hud.connections.models import Connection
from jarvis_hud.connections.providers import Provider, ProviderError
from jarvis_hud.db import get_db
from jarvis_hud.plugins import Plugin

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]


def providers(request: Request) -> list[tuple[Plugin, Provider]]:
    plugins = sorted(request.app.state.plugins.values(), key=lambda p: (p.position, p.id))
    return [(plugin, provider) for plugin in plugins for provider in plugin.connections()]


def _provider(request: Request, provider_id: str) -> tuple[Plugin, Provider]:
    found = next(((pl, pr) for pl, pr in providers(request) if pr.id == provider_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    return found


def forget_cached(request: Request, provider_id: str, connection_id: int) -> None:
    request.app.state.cache.invalidate(f"{provider_id}:{connection_id}:")


def _form(
    request: Request,
    plugin: Plugin,
    provider: Provider,
    values: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    instructions = f"{plugin.id}/{provider.instructions}" if provider.instructions else None
    return request.app.state.templates.TemplateResponse(
        request,
        "integrations/connect.html",
        {
            "provider": provider,
            "instructions": instructions,
            # Secrets are never sent back to the browser.
            "values": {
                f.name: (values or {}).get(f.name, "") for f in provider.fields if not f.secret
            },
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/integrations/{provider_id}/connect", response_class=HTMLResponse)
async def connect_page(request: Request, provider_id: str) -> Response:
    plugin, provider = _provider(request, provider_id)
    return _form(request, plugin, provider)


@router.post("/integrations/{provider_id}/connect")
async def connect(request: Request, db: Db, provider_id: str) -> Response:
    plugin, provider = _provider(request, provider_id)
    form = await request.form()
    values = {f.name: str(form.get(f.name, "")).strip() for f in provider.fields}
    if missing := [f.label for f in provider.fields if not values[f.name]]:
        return _form(request, plugin, provider, values, f"Fill in: {', '.join(missing)}.", 400)
    try:
        verified = await provider.verify(request.app.state.http, values)
    except ProviderError as error:
        return _form(request, plugin, provider, values, str(error), 400)
    if not provider.multiple:
        for existing in await store.list_connections(db, provider.id):
            if existing.account != verified.account:
                await store.delete_connection(db, existing)
    connection = await store.save_connection(
        db,
        request.app.state.secret_box,
        provider.id,
        verified.account,
        verified.credentials,
        "",
    )
    await store.save_check(db, connection, _capabilities(verified.capabilities))
    forget_cached(request, provider.id, connection.id)
    return RedirectResponse(f"/integrations?status=connected&provider={provider.id}", 303)


def _capabilities(capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    account = {"id": "account", "label": "Access to the account", "ok": True}
    return [account, *capabilities]


async def _connection_or_404(db: AsyncSession, provider_id: str, connection_id: int) -> Connection:
    connection = await store.get_connection(db, provider_id, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


@router.post("/integrations/{provider_id}/{connection_id}/test")
async def test(request: Request, db: Db, provider_id: str, connection_id: int) -> Response:
    _, provider = _provider(request, provider_id)
    connection = await _connection_or_404(db, provider_id, connection_id)
    credentials = request.app.state.secret_box.decrypt(connection.credentials)
    try:
        verified = await provider.verify(request.app.state.http, credentials)
        capabilities = _capabilities(verified.capabilities)
    except ProviderError as error:
        capabilities = [
            {"id": "account", "label": "Access to the account", "ok": False, "detail": str(error)}
        ]
    await store.save_check(db, connection, capabilities)
    forget_cached(request, provider_id, connection_id)
    return RedirectResponse("/integrations?status=tested", status_code=303)


@router.post("/integrations/{provider_id}/{connection_id}/disconnect")
async def disconnect(request: Request, db: Db, provider_id: str, connection_id: int) -> Response:
    _provider(request, provider_id)
    connection = await _connection_or_404(db, provider_id, connection_id)
    await store.delete_connection(db, connection)
    forget_cached(request, provider_id, connection_id)
    return RedirectResponse(f"/integrations?status=removed&provider={provider_id}", 303)
