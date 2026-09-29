# gaps-python

Patterns taken from a real repository (full-stack-fastapi-template), added here first (see
CLAUDE.md, "Fixture first"). A pattern the analyzer does not handle yet goes into
`MANIFEST.yaml` with `known_gap` set: the benchmark reports it and the CI gate skips it. Once
the analyzer handles it, `known_gap` is removed and the gate covers it.

| Flow | Pattern | Status |
|---|---|---|
| G01 | Signup email and name sent over SMTP with `smtplib`, through an `EmailMessage` | handled (#2) |
| G02 | Session injected with an `Annotated[Session, Depends()]` alias, `session.add` | handled |
| G03 | `session.exec(select(Model))` read, row fields logged | handled (#2) |
| G04 | `select(Model).where(...)` bound to a variable, `table=True` class with a schema base | handled (#2) |
