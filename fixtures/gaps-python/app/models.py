from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    full_name: str | None = None


class Subscriber(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str
    topic: str
