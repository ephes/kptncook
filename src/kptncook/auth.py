"""Shared admission for opaque credentials used in HTTP headers."""

MAX_TOKEN_LENGTH = 8192


def validate_token(token: object) -> str:
    """Return an unchanged visible-ASCII token or a value-free failure."""
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= MAX_TOKEN_LENGTH
        or any(not 0x21 <= ord(character) <= 0x7E for character in token)
    ):
        raise ValueError("Invalid authentication token")
    return token
