"""Encryption at rest for GitHub tokens (Fernet: AES-128-CBC with HMAC-SHA256)."""

from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken


class TokenCipherError(ValueError):
    pass


GENERATE_HINT = (
    "generate one with `python -c 'from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())'`"
)
MIN_SECRET_CHARS = 32


def fernet_key(key: str) -> bytes:
    """The Fernet key for LANTERN_TOKEN_KEY.

    A Fernet key is used as given. Hosts generate secrets in their own formats (Render's
    `generateValue` is base64 of 256 random bits), so any other secret of at least 32
    characters is accepted and hashed into a key. The API and the worker derive the same key
    from the same value.
    """
    value = key.strip()
    if not value:
        raise TokenCipherError(f"LANTERN_TOKEN_KEY is not set; {GENERATE_HINT}")
    try:
        Fernet(value.encode())
    except ValueError:
        if len(value) < MIN_SECRET_CHARS:
            raise TokenCipherError(
                f"LANTERN_TOKEN_KEY is neither a Fernet key nor a random secret of at least "
                f"{MIN_SECRET_CHARS} characters; {GENERATE_HINT}"
            ) from None
        return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest())
    return value.encode()


class TokenCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(fernet_key(key))

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
