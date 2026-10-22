"""A software WebAuthn authenticator for testing passkey registration and sign-in."""

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftAuthenticator:
    def __init__(self, origin: str = "http://testserver") -> None:
        self.origin = origin
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.sign_count = 0
        self.user_handle = b""

    def _client_data(self, kind: str, challenge: str) -> bytes:
        data = {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        return json.dumps(data).encode()

    def create(self, options: dict[str, Any]) -> dict[str, Any]:
        """Respond to registration options, like navigator.credentials.create()."""
        self.user_handle = base64.urlsafe_b64decode(options["user"]["id"] + "==")
        numbers = self.private_key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        attested = (
            bytes(16)  # AAGUID
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + cose_key
        )
        flags = FLAG_USER_PRESENT | FLAG_USER_VERIFIED | FLAG_ATTESTED_CREDENTIAL
        auth_data = self._rp_id_hash(options["rp"]["id"]) + bytes([flags]) + bytes(4) + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(
                    self._client_data("webauthn.create", options["challenge"])
                ),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict[str, Any]) -> dict[str, Any]:
        """Respond to authentication options, like navigator.credentials.get()."""
        self.sign_count += 1
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = (
            self._rp_id_hash(options["rpId"])
            + bytes([FLAG_USER_PRESENT | FLAG_USER_VERIFIED])
            + self.sign_count.to_bytes(4, "big")
        )
        signature = self.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_handle),
            },
            "clientExtensionResults": {},
        }

    @staticmethod
    def _rp_id_hash(rp_id: str) -> bytes:
        return hashlib.sha256(rp_id.encode()).digest()
