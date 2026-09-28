from collections.abc import Iterator

from sqlmodel import Session, create_engine

from app.config import settings

engine = create_engine(settings.database_url)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
