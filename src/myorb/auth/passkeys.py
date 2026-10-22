import json
import time
from typing import Annotated, Any

import webauthn
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from myorb.auth import store
from myorb.auth.gate import safe_next
from myorb.auth.sessions import expected_origin, is_secure, relying_party_id, start_session
from myorb.auth.tokens import sign, unsign
from myorb.db import get_db

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

CHALLENGE_COOKIE = "myorb_passkey_challenge"
CHALLENGE_MAX_AGE = 5 * 60
REGISTER_PURPOSE = "passkey-register"
LOGIN_PURPOSE = "passkey-login"
# myOrb has a single user, so every passkey belongs to the same WebAuthn user handle.
USER_ID = b"myorb-owner"


class PasskeyRegistration(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    credential: dict[str, Any]


class PasskeyLogin(BaseModel):
    credential: dict[str, Any]
    next: str | None = None


def _options_response(request: Request, options: Any, purpose: str) -> Response:
    """Return WebAuthn options and remember their challenge in a signed, short-lived cookie."""
    key = request.app.state.auth.session_key
    token = sign(key, purpose, {"challenge": bytes_to_base64url(options.challenge)}, time.time())
    response = JSONResponse(json.loads(webauthn.options_to_json(options)))
    response.set_cookie(
        CHALLENGE_COOKIE,
        token,
        max_age=CHALLENGE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=is_secure(request),
    )
    return response


def _expected_challenge(request: Request, purpose: str) -> bytes:
    token = request.cookies.get(CHALLENGE_COOKIE)
    key = request.app.state.auth.session_key
    data = token and unsign(key, token, purpose, CHALLENGE_MAX_AGE, time.time())
    if not data:
        raise HTTPException(status_code=400, detail="The passkey request expired. Try again.")
    return base64url_to_bytes(data["challenge"])


@router.post("/settings/security/passkeys/options")
async def registration_options(request: Request, db: Db) -> Response:
    existing = await store.list_passkeys(db)
    options = webauthn.generate_registration_options(
        rp_id=relying_party_id(request),
        rp_name="myOrb",
        user_id=USER_ID,
        user_name="myorb",
        user_display_name="myOrb",
        exclude_credentials=[PublicKeyCredentialDescriptor(id=p.credential_id) for p in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return _options_response(request, options, REGISTER_PURPOSE)


@router.post("/settings/security/passkeys")
async def register_passkey(request: Request, db: Db, body: PasskeyRegistration) -> Response:
    challenge = _expected_challenge(request, REGISTER_PURPOSE)
    try:
        verified = webauthn.verify_registration_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=relying_party_id(request),
            expected_origin=expected_origin(request),
            require_user_verification=True,
        )
    except (WebAuthnException, KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail="The passkey could not be verified.") from error
    await store.add_passkey(
        db, body.name, verified.credential_id, verified.credential_public_key, verified.sign_count
    )
    response = JSONResponse({"ok": True})
    response.delete_cookie(CHALLENGE_COOKIE)
    return response


@router.post("/login/passkey/options")
async def authentication_options(request: Request) -> Response:
    options = webauthn.generate_authentication_options(
        rp_id=relying_party_id(request),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return _options_response(request, options, LOGIN_PURPOSE)


@router.post("/login/passkey/verify")
async def login_with_passkey(request: Request, db: Db, body: PasskeyLogin) -> Response:
    challenge = _expected_challenge(request, LOGIN_PURPOSE)
    try:
        passkey = await store.find_passkey(db, base64url_to_bytes(body.credential["rawId"]))
        if passkey is None:
            raise HTTPException(status_code=400, detail="This passkey is not registered.")
        verified = webauthn.verify_authentication_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=relying_party_id(request),
            expected_origin=expected_origin(request),
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except (WebAuthnException, KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail="The passkey could not be verified.") from error
    await store.record_passkey_use(db, passkey, verified.new_sign_count)
    response = JSONResponse({"redirect": safe_next(body.next)})
    response.delete_cookie(CHALLENGE_COOKIE)
    start_session(response, request, request.app.state.auth)
    return response
