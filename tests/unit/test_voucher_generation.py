"""Unit tests for voucher code generation (Req 1.1)."""

from app.services.voucher_service import ALLOWED_CHARS, generate_voucher_code


def test_character_set_and_length():
    """Every generated code is 12 chars drawn from the allowed set."""
    for _ in range(1000):
        code = generate_voucher_code()
        assert len(code) == 12
        assert all(c in ALLOWED_CHARS for c in code)


def test_forbidden_characters_never_appear():
    """Ambiguous characters I, O, 0, 1 must never be generated."""
    forbidden = set("IO01")
    for _ in range(1000):
        assert not (set(generate_voucher_code()) & forbidden)


def test_no_duplicates_in_1000_codes():
    """Sanity check against the birthday paradox (32^12 space)."""
    codes = {generate_voucher_code() for _ in range(1000)}
    assert len(codes) == 1000


def test_allowed_charset_is_exactly_32():
    """The alphabet is A-Z minus I/O plus 2-9 (8 digits)."""
    assert len(ALLOWED_CHARS) == 32
    assert set("IO01").isdisjoint(ALLOWED_CHARS)
