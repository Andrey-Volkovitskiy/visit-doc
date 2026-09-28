"""The connector's secrets: how they are made, and the only form in which they are kept.

Every code, token and request id is stored as its SHA-256 digest and never in plain
form, so a lookup is always by `digest(presented)` and nothing stored can be read back.
"""

import hashlib
import secrets

# Crockford's base32: digits and upper-case letters without I, L, O and U, so a code
# read aloud or copied by eye has no pair of characters to confuse.
PAIRING_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PAIRING_CODE_LENGTH = 8
_SECRET_BYTES = 32


def new_secret() -> str:
    """Return a fresh URL-safe secret of 32 random bytes (43 characters)."""
    return secrets.token_urlsafe(_SECRET_BYTES)


def digest(value: str) -> str:
    """Return the SHA-256 hex digest of `value`, the only form a secret is kept in."""
    return hashlib.sha256(value.encode()).hexdigest()


def new_pairing_code() -> str:
    """Return a fresh pairing code as a person is shown it, `XXXX-XXXX`."""
    characters = "".join(
        secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(_PAIRING_CODE_LENGTH)
    )
    half = _PAIRING_CODE_LENGTH // 2
    return f"{characters[:half]}-{characters[half:]}"


def normalize_pairing_code(typed: str) -> str:
    """Return a pairing code in the form its digest is taken of.

    Upper-cased, with the hyphen and surrounding whitespace removed, so a code typed in
    lower case or without its hyphen is the same code.
    """
    return typed.strip().upper().replace("-", "")
