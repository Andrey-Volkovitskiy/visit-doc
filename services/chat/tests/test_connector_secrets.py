"""The connector's secrets: how they are made, stored and compared.

Every secret is stored as its SHA-256 digest and nothing else, so what these helpers
produce is exactly what the tables hold and what a lookup matches on.
"""

import re
import string

import pytest
from chat.connectors.pkce import s256_matches
from chat.connectors.secrets import (
    PAIRING_CODE_ALPHABET,
    digest,
    new_pairing_code,
    new_secret,
    normalize_pairing_code,
)

# RFC 7636 Appendix B.
_RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
_RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

_URL_SAFE = set(string.ascii_letters + string.digits + "-_")


def test_a_secret_is_32_random_bytes_url_safe() -> None:
    secret = new_secret()

    assert len(secret) == 43
    assert set(secret) <= _URL_SAFE


def test_secrets_do_not_repeat() -> None:
    assert len({new_secret() for _ in range(100)}) == 100


def test_a_digest_is_64_lowercase_hex_characters() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", digest("anything"))


def test_a_digest_is_sha256() -> None:
    assert digest("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_the_pairing_alphabet_is_crockford_base32() -> None:
    # No I, L, O or U: nothing a person reading the code aloud can confuse.
    assert PAIRING_CODE_ALPHABET == "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def test_a_pairing_code_is_eight_crockford_characters_shown_as_two_fours() -> None:
    for _ in range(50):
        code = new_pairing_code()

        assert re.fullmatch(r"[0-9A-Z]{4}-[0-9A-Z]{4}", code)
        assert set(code.replace("-", "")) <= set(PAIRING_CODE_ALPHABET)


@pytest.mark.parametrize(
    "typed", ["K7QM-4XPD", "k7qm-4xpd", "K7QM4XPD", "k7qm4xpd", " K7QM-4XPD "]
)
def test_a_code_is_normalized_whatever_its_case_and_hyphen(typed: str) -> None:
    assert normalize_pairing_code(typed) == "K7QM4XPD"


def test_the_rfc_7636_vector_matches() -> None:
    assert s256_matches(_RFC_VERIFIER, _RFC_CHALLENGE)


@pytest.mark.parametrize(
    "verifier",
    [
        _RFC_VERIFIER[:-1] + "l",
        _RFC_CHALLENGE,
        "",
        # The right verifier, but under RFC 7636's 43-character minimum once cut.
        _RFC_VERIFIER[:42],
    ],
)
def test_any_other_verifier_does_not_match(verifier: str) -> None:
    assert not s256_matches(verifier, _RFC_CHALLENGE)
