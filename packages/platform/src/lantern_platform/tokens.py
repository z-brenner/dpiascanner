"""Installation tokens cached in the database, encrypted at rest."""

from __future__ import annotations

import datetime as dt

from lantern_platform.crypto import TokenCipher
from lantern_platform.db import Database, Installation


def aware(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes; every stored time is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


class DatabaseTokenStore:
    def __init__(self, db: Database, cipher: TokenCipher) -> None:
        self.db = db
        self.cipher = cipher

    def load(self, installation_id: int) -> tuple[str, dt.datetime] | None:
        with self.db.session() as s:
            row = s.get(Installation, installation_id)
            if row is None or not row.token_encrypted or row.token_expires_at is None:
                return None
            return self.cipher.decrypt(row.token_encrypted), aware(row.token_expires_at)

    def save(self, installation_id: int, token: str, expires_at: dt.datetime) -> None:
        with self.db.session() as s:
            row = s.get(Installation, installation_id)
            if row is None:
                row = Installation(id=installation_id, account_login="")
                s.add(row)
            row.token_encrypted = self.cipher.encrypt(token)
            row.token_expires_at = expires_at
