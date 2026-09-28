"""Database models (SQLAlchemy 2). Postgres in deployment, SQLite in tests.

Tables follow the data model in CLAUDE.md (run, node, edge, decision, finding, report) plus
what the GitHub integration needs: users, sessions, installations, the PR comment each pull
request gets, and webhook delivery ids for idempotency. Tokens are stored encrypted
(``crypto.TokenCipher``); sessions are stored by hash. Nodes keep only redacted, bounded
snippets; no other repository content is stored (see ``storage.scrub``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionRow(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Installation(Base):
    __tablename__ = "installations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub installation id
    account_login: Mapped[str] = mapped_column(String(100))
    account_type: Mapped[str] = mapped_column(String(20), default="User")
    repository_selection: Mapped[str] = mapped_column(String(20), default="selected")
    suspended: Mapped[bool] = mapped_column(default=False)
    token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UserInstallation(Base):
    __tablename__ = "user_installations"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    installation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("installations.id", ondelete="CASCADE"), primary_key=True
    )


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    repo_full_name: Mapped[str] = mapped_column(String(200), index=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ref: Mapped[str] = mapped_column(String(255))
    sha: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(
        String(20), default="manual"
    )  # manual | push | pull_request
    mode: Mapped[str] = mapped_column(String(20), default="full")  # full | incremental
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    changed_files: Mapped[list[str]] = mapped_column(JSONType, default=list)
    options: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    current_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    graph_meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    question_set_version: Mapped[str] = mapped_column(String(20), default="v1")
    provider: Mapped[str] = mapped_column(String(100), default="")


class RunStage(Base):
    __tablename__ = "run_stages"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(20), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(20), default="running"
    )  # running | done | failed | skipped
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    info: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class NodeRow(Base):
    __tablename__ = "nodes"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    file: Mapped[str] = mapped_column(String(500))
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(20))
    snippet: Mapped[str] = mapped_column(Text, default="")  # redacted and bounded
    snippet_hash: Mapped[str] = mapped_column(String(64), default="")
    rule_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class EdgeRow(Base):
    __tablename__ = "edges"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    from_node: Mapped[str] = mapped_column(String(40))
    to_node: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class DecisionRowDB(Base):
    __tablename__ = "decisions"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(40), index=True)
    target_type: Mapped[str] = mapped_column(String(20))
    question_set_id: Mapped[str] = mapped_column(String(40))
    question_set_version: Mapped[str] = mapped_column(String(20))
    question_id: Mapped[str] = mapped_column(String(60))
    answer: Mapped[str] = mapped_column(String(60))
    probability: Mapped[float] = mapped_column(Float)
    distribution: Mapped[dict[str, float]] = mapped_column(JSONType, default=dict)
    provider: Mapped[str] = mapped_column(String(100))
    provider_version: Mapped[str] = mapped_column(String(100))
    state_hash: Mapped[str] = mapped_column(String(64))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class FindingRow(Base):
    __tablename__ = "findings"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    key: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    verification: Mapped[str] = mapped_column(String(30), default="not-run")
    title: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSONType)

    __table_args__ = (Index("ix_findings_run_filters", "run_id", "category", "severity", "status"),)


class ReportRow(Base):
    __tablename__ = "reports"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    format: Mapped[str] = mapped_column(String(10), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(100))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PullRequestComment(Base):
    __tablename__ = "pr_comments"
    repo_full_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    pr_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event: Mapped[str] = mapped_column(String(40))
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A transaction: committed on success, rolled back on any exception."""
        session = self.sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _sqlite_pragmas(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
