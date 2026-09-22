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

The redaction rule is exported on its own as well, for anything else that must not
carry a secret out of the process - the chat service's trace export applies it to every
span, so the log and the trace cannot come to disagree about what a secret is.
"""

from shared_logging.logging import (
    LogFormat,
    LogLevel,
    SafeLogger,
    configure_logging,
    get_logger,
    is_secret_key,
    known_secret_values,
    make_redact_secrets_processor,
    redact_value,
)

__all__ = [
    "LogFormat",
    "LogLevel",
    "SafeLogger",
    "configure_logging",
    "get_logger",
    "is_secret_key",
    "known_secret_values",
    "make_redact_secrets_processor",
    "redact_value",
]
