def normalize_email(value: str) -> str:
    local, _, domain = value.strip().partition("@")
    return f"{local}@{domain.lower()}"
