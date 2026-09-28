"""Encryption at rest for GitHub tokens (Fernet: AES-128-CBC with HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken


class TokenCipherError(ValueError):
    pass


class TokenCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise TokenCipherError(
                "LANTERN_TOKEN_KEY is not set; generate one with `python -c 'from "
                "cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
            )
        self._fernet = Fernet(key.encode())

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode()).decode()

    def decrypt(self, blob: str) -> str:
        try:
            return self._fernet.decrypt(blob.encode()).decode()
        except InvalidToken as exc:
            raise TokenCipherError("stored token cannot be decrypted with this key") from exc


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Sessions are stored by hash, so a database leak does not yield usable sessions."""
    return hashlib.sha256(token.encode()).hexdigest()
