"""Property 18: Admin password always bcrypt-hashed.

Feature: aruba-voucher-backend, Property 18.

_For any_ password P, ``hash_password(P)`` is a valid bcrypt hash with cost
factor >= 10, and the plaintext P does not appear verbatim in the stored hash.

Validates: Requirements 11.2
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.auth_service import hash_password, verify_password

# Passwords that are short enough to appear by chance inside the fixed bcrypt
# header ("$2b$12$") are excluded — the header necessarily contains digits and
# the characters "2b". Long randomly-salted hashes cannot contain a >=8 char
# password by accident. Surrogates and NUL are excluded because bcrypt cannot
# encode them.
_PASSWORDS = st.text(
    min_size=8,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
).filter(lambda s: len(s.encode("utf-8")) <= 72)


@given(password=_PASSWORDS)
@settings(max_examples=25, deadline=None)
def test_admin_password_always_bcrypt_hashed(password):
    hashed = hash_password(password)

    # Valid bcrypt hash, modern prefix.
    assert hashed.startswith("$2b$")

    # Cost factor is encoded as the second field: $2b$<cost>$...
    cost = int(hashed.split("$")[2])
    assert cost >= 10

    # Plaintext never appears in the stored hash.
    assert password not in hashed

    # The hash round-trips.
    assert verify_password(password, hashed) is True
