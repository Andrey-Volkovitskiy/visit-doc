"""PKCE with S256, the only method the connector accepts."""

import base64
import hashlib
import hmac

# RFC 7636 §4.1's bounds on a code verifier's length.
_MIN_VERIFIER_LENGTH = 43
_MAX_VERIFIER_LENGTH = 128


def s256_matches(verifier: str, challenge: str) -> bool:
    """Return whether `verifier` is the one `challenge` was derived from.

    A verifier outside RFC 7636's 43-128 characters never matches. The comparison is
    constant-time, so a refusal says nothing about how close the verifier came.
    """
    if not _MIN_VERIFIER_LENGTH <= len(verifier) <= _MAX_VERIFIER_LENGTH:
        return False
    derived = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii", "replace")).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return hmac.compare_digest(derived.encode(), challenge.encode("utf-8", "replace"))
