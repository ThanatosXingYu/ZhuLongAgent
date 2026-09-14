from __future__ import annotations

import re

import pytest

from internal.ichunqiu_crypto import (
    MatchURLValidationError,
    build_request_payload,
    create_request_id,
    encrypt_login_field,
    parse_match_url,
    request_signature,
)


def test_parse_match_url_accepts_slug_and_entry_parameter() -> None:
    slug = parse_match_url(" https://match.ichunqiu.com/wanqubei/ ")
    assert slug.normalized_url == "https://match.ichunqiu.com/wanqubei"
    assert slug.event_slug == "wanqubei"
    assert slug.entry_key == ""
    assert slug.encrypted_binding is False

    entry = parse_match_url("https://match.ichunqiu.com/index?k=demo_Key-123")
    assert entry.normalized_url == "https://match.ichunqiu.com/index?k=demo_Key-123"
    assert entry.binding_value == "demo_Key-123"
    assert entry.encrypted_binding is True


@pytest.mark.parametrize(
    "value",
    [
        "http://match.ichunqiu.com/wanqubei",
        "https://example.test/wanqubei",
        "https://match.ichunqiu.com@evil.test/wanqubei",
        "https://match.ichunqiu.com:8443/wanqubei",
        "https://match.ichunqiu.com/a/b",
        "https://match.ichunqiu.com/index?k=first&k=second",
        "https://match.ichunqiu.com/index?k=bad%2Fvalue",
    ],
)
def test_parse_match_url_rejects_untrusted_locations(value: str) -> None:
    with pytest.raises(MatchURLValidationError):
        parse_match_url(value)


def test_signature_and_common_payload_match_wire_rules() -> None:
    payload = build_request_payload(
        {"page_index": 1, "page_size": 100},
        "match_demo",
        "login:match_demo:not-a-real-token",
        timestamp_ms=1_700_000_000_000,
        request_id="0123456789abcdef0123456789abcdef",
    )
    assert payload == {
        "page_index": 1,
        "page_size": 100,
        "k": "match_demo",
        "stamp": 1_700_000_000_000,
        "token": "login:match_demo:not-a-real-token",
        "rs": "0123456789abcdef0123456789abcdef",
    }
    assert request_signature(payload) == "dc96b0298628e77e33d55213769b2b7c750d2faf"


@pytest.mark.parametrize(
    ("plain", "encrypted"),
    [
        ("demo-account", "CNApD92V5OkjpA-c0ok3hw"),
        ("password123", "vVRhIPfcDM3_avGchWcaqw"),
        ("密码123", "RK9qjaCPivbrehCtoC7kDA"),
        ("13800138000", "gLdILNSAWgWJV5lVGfQnYA"),
        ("123456", "L6R0AXo-6dXOnzMC"),
    ],
)
def test_login_encryption_matches_public_frontend(plain: str, encrypted: str) -> None:
    assert encrypt_login_field(plain) == encrypted


def test_request_id_is_random_lowercase_hex() -> None:
    first = create_request_id()
    second = create_request_id()
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert first != second
