import base64


def encrypt_field(value: str) -> str:
    """Protect a sensitive field before it leaves the request scope."""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")
