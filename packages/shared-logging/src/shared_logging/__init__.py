"""The one structlog configuration every service logs through.

Every log call in every service flows through one processor chain (merge correlation id
-> add level -> add timestamp -> truncate -> redact -> render), so changing how logs are
shaped or rendered later is a one-line change here rather than a rewrite of every call
site - or, worse, the same change made twice and eventually made only once.

Redaction in particular is a security control, and a per-service copy of it is a control
that can silently diverge: a bypass fixed in one service's copy would leave the other
logging that value in the clear, with nothing to catch the difference. Each service
supplies only what genuinely varies - the names of its own secret-bearing settings
fields - and inherits the chain itself.
"""

from shared_logging.logging import (
    SafeLogger,
    configure_logging,
    get_logger,
    make_redact_secrets_processor,
)

__all__ = [
    "SafeLogger",
    "configure_logging",
    "get_logger",
    "make_redact_secrets_processor",
]
