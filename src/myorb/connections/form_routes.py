"""Pages and actions for form-based connections (API tokens, URLs) declared by plugins."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.connections import store
from myorb.connections.models import Connection
from myorb.connections.providers import HIDDEN, Provider, ProviderError
from myorb.db import get_db
from myorb.plugins import Plugin

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


async def connections_changed(request: Request, plugin: Plugin) -> None:
    """Let the plugin know, e.g. to show a new source's circle around the orb."""
    await plugin.connections_changed(request.app.state.connections)


def _form(
    request: Request,
    plugin: Plugin,
    provider: Provider,
    values: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
    editing: Connection | None = None,
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
            # Editing a connection: the same form, filled in; secrets left empty are kept.
            "editing": editing,
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
    if missing := [f.label for f in provider.fields if f.required and not values[f.name]]:
        return _form(request, plugin, provider, values, f"Fill in: {', '.join(missing)}.", 400)
    try:
        verified = await provider.verify(request.app.state.http, values)
    except ProviderError as error:
        return _form(request, plugin, provider, values, str(error), 400)
    box = request.app.state.secret_box
    credentials = verified.credentials
    for existing in await store.list_connections(db, provider.id):
        if existing.account == verified.account:
            # Connecting the same account again (e.g. a new token) keeps what was left out.
            if left_out := box.decrypt(existing.credentials).get(HIDDEN):
                credentials = {**credentials, HIDDEN: left_out}
        elif not provider.multiple:
            await store.delete_connection(db, existing)
    connection = await store.save_connection(
        db, box, provider.id, verified.account, credentials, ""
    )
    await store.save_check(db, connection, _capabilities(verified.capabilities))
    forget_cached(request, provider.id, connection.id)
    await connections_changed(request, plugin)
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
    plugin, _ = _provider(request, provider_id)
    connection = await _connection_or_404(db, provider_id, connection_id)
    await store.delete_connection(db, connection)
    forget_cached(request, provider_id, connection_id)
    await connections_changed(request, plugin)
    return RedirectResponse(f"/integrations?status=removed&provider={provider_id}", 303)


@router.get("/integrations/{provider_id}/{connection_id}/choices", response_class=HTMLResponse)
async def choices_page(request: Request, db: Db, provider_id: str, connection_id: int) -> Response:
    """Which parts of the account to show, e.g. the workspaces still in use."""
    _, provider = _provider(request, provider_id)
    if not provider.choices_label:
        raise HTTPException(status_code=404, detail="Nothing to choose")
    connection = await _connection_or_404(db, provider_id, connection_id)
    credentials = request.app.state.secret_box.decrypt(connection.credentials)
    error = None
    try:
        choices = await provider.choices(request.app.state.http, credentials)
    except ProviderError as exc:
        choices, error = [], str(exc)
    return request.app.state.templates.TemplateResponse(
        request,
        "integrations/choices.html",
        {
            "provider": provider,
            "connection": connection,
            "choices": choices,
            "hidden": set(credentials.get(HIDDEN) or []),
            "error": error,
        },
    )


@router.post("/integrations/{provider_id}/{connection_id}/choices")
async def save_choices(request: Request, db: Db, provider_id: str, connection_id: int) -> Response:
    _, provider = _provider(request, provider_id)
    if not provider.choices_label:
        raise HTTPException(status_code=404, detail="Nothing to choose")
    connection = await _connection_or_404(db, provider_id, connection_id)
    box = request.app.state.secret_box
    credentials = box.decrypt(connection.credentials)
    form = await request.form()
    shown = set(form.getlist("shown"))
    # Only the parts offered on the page: what was left out stays left out, new ones show.
    offered = set(form.getlist("offered"))
    left_out = (set(credentials.get(HIDDEN) or []) - offered) | (offered - shown)
    await store.save_connection(
        db,
        box,
        provider_id,
        connection.account,
        {**credentials, HIDDEN: sorted(left_out)},
        connection.scopes,
    )
    forget_cached(request, provider_id, connection_id)
    return RedirectResponse(f"/integrations?status=choices&provider={provider_id}", 303)


def _stored_values(provider: Provider, credentials: dict[str, Any]) -> dict[str, str]:
    """A connection's form values as stored, for its edit form (secrets stay on the server)."""
    return {f.name: str(credentials.get(f.name, "")) for f in provider.fields if not f.secret}


@router.get("/integrations/{provider_id}/{connection_id}/edit", response_class=HTMLResponse)
async def edit_page(request: Request, db: Db, provider_id: str, connection_id: int) -> Response:
    plugin, provider = _provider(request, provider_id)
    connection = await _connection_or_404(db, provider_id, connection_id)
    credentials = request.app.state.secret_box.decrypt(connection.credentials)
    values = _stored_values(provider, credentials)
    return _form(request, plugin, provider, values, editing=connection)


@router.post("/integrations/{provider_id}/{connection_id}/edit")
async def edit(request: Request, db: Db, provider_id: str, connection_id: int) -> Response:
    """Save a connection's changed values: verified again like a new one, the same row kept."""
    plugin, provider = _provider(request, provider_id)
    connection = await _connection_or_404(db, provider_id, connection_id)
    box = request.app.state.secret_box
    stored = box.decrypt(connection.credentials)
    form = await request.form()
    values = {f.name: str(form.get(f.name, "")).strip() for f in provider.fields}
    for field in provider.fields:
        if field.secret and not values[field.name]:
            values[field.name] = str(stored.get(field.name, ""))  # left empty: unchanged
    if missing := [f.label for f in provider.fields if f.required and not values[f.name]]:
        return _form(
            request, plugin, provider, values, f"Fill in: {', '.join(missing)}.", 400, connection
        )
    try:
        verified = await provider.verify(request.app.state.http, values)
    except ProviderError as error:
        return _form(request, plugin, provider, values, str(error), 400, connection)
    for other in await store.list_connections(db, provider.id):
        if other.id != connection.id and other.account == verified.account:
            message = f"{verified.account} is already connected."
            return _form(request, plugin, provider, values, message, 400, connection)
    credentials = verified.credentials
    if left_out := stored.get(HIDDEN):
        credentials = {**credentials, HIDDEN: left_out}
    connection.account = verified.account
    connection.credentials = box.encrypt(credentials)
    await store.save_check(db, connection, _capabilities(verified.capabilities))
    forget_cached(request, provider.id, connection.id)
    await connections_changed(request, plugin)
    return RedirectResponse(f"/integrations?status=saved-connection&provider={provider.id}", 303)
