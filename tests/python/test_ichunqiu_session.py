from __future__ import annotations

import pytest

from internal.ichunqiu_session import (
    PORTABLE_SESSION_PREFIX,
    SessionImportError,
    browser_login_script,
    encode_portable_session,
    parse_session_request,
)


def test_parse_raw_http_request_extracts_token_cookie_match_and_url() -> None:
    raw = """POST /match/detail HTTP/1.1
Host: apiterminator.ichunqiu.com
Cookie: PHPSESSID=session-value; theme=dark
Referer: https://match.ichunqiu.com/2026qwbs10
Content-Type: application/json

{"url_key":"server-match-key","token":"login:demo-token","is_encrypt":1}
"""

    imported = parse_session_request(raw)

    assert imported.token == "login:demo-token"
    assert imported.cookies == "PHPSESSID=session-value; theme=dark"
    assert imported.match_key == "server-match-key"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_parse_curl_request_extracts_authorization_and_referer() -> None:
    raw = """curl 'https://apiterminator.ichunqiu.com/match/team/info' \\
  -H 'Authorization: Bearer bearer-token' \\
  -H 'Cookie: PHPSESSID=session-value' \\
  -H 'Referer: https://match.ichunqiu.com/2026qwbs10/section' \\
  --data-raw '{"k":"request-key","team_id":""}'"""

    imported = parse_session_request(raw)

    assert imported.token == "bearer-token"
    assert imported.cookies == "PHPSESSID=session-value"
    assert imported.match_key == "request-key"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_parse_session_rejects_request_without_auth_material() -> None:
    with pytest.raises(SessionImportError, match="token"):
        parse_session_request(
            "GET /match/detail HTTP/1.1\nHost: apiterminator.ichunqiu.com\n\n"
        )


def test_parse_escaped_request_with_continuations_cookie_and_body() -> None:
    raw = (
        "POST /match/blue/query_status HTTP/1.1\\\n"
        "Host: apiterminator.ichunqiu.com\\\n"
        "Cookie: PHPSESSID=session-value\\\n"
        "Referer: https\\://match.ichunqiu.com/2026qwbs10\\\n"
        "Content-Type: application/json\\\n"
        "Content-Length: 63\\\n"
        "\\\n"
        '{"k":"match-key","token":"login:test-token"}'
    )

    imported = parse_session_request(raw)

    assert imported.cookies == "PHPSESSID=session-value"
    assert imported.token == "login:test-token"
    assert imported.match_key == "match-key"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_parse_rejects_content_length_without_request_body() -> None:
    raw = (
        "POST /match/blue/query_status HTTP/1.1\\\n"
        "Referer: https\\://match.ichunqiu.com/\\\n"
        "Content-Length: 186"
    )

    with pytest.raises(SessionImportError, match="请求体不完整"):
        parse_session_request(raw)


def test_parse_accepts_cookie_when_content_length_has_no_body() -> None:
    raw = (
        "POST /match/blue/query_status HTTP/1.1\\\n"
        "Cookie: PHPSESSID=session-value\\\n"
        "Referer: https\\://match.ichunqiu.com/2026qwbs10\\\n"
        "Content-Length: 186"
    )

    imported = parse_session_request(raw)

    assert imported.cookies == "PHPSESSID=session-value"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_parse_accepts_cookie_text_without_http_request_line() -> None:
    raw = (
        "PHPSESSID=session-value; theme=dark; k=match-key; "
        "https://match.ichunqiu.com/2026qwbs10"
    )

    imported = parse_session_request(raw)

    assert imported.cookies == "PHPSESSID=session-value"
    assert imported.match_key == "match-key"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_parse_accepts_header_text_without_http_request_line() -> None:
    raw = """Cookie: PHPSESSID=session-value
Referer: https://match.ichunqiu.com/2026qwbs10
Authorization: Bearer bearer-token"""

    imported = parse_session_request(raw)

    assert imported.cookies == "PHPSESSID=session-value"
    assert imported.token == "bearer-token"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_parse_accepts_plain_token_text() -> None:
    imported = parse_session_request("当前登录态 token=login:demo-token")

    assert imported.token == "login:demo-token"


def test_parse_accepts_json_text_without_http_request_line() -> None:
    raw = (
        '{"token":"login:demo-token","k":"match-key",'
        '"match_url":"https://match.ichunqiu.com/2026qwbs10"}'
    )

    imported = parse_session_request(raw)

    assert imported.token == "login:demo-token"
    assert imported.match_key == "match-key"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_parse_accepts_nested_login_response_json() -> None:
    raw = (
        '{"code":0,"data":{"user":{"token":"login:demo-token"},'
        '"url_key":"match-key"}}'
    )

    imported = parse_session_request(raw)

    assert imported.token == "login:demo-token"
    assert imported.match_key == "match-key"


def test_parse_accepts_percent_encoded_local_storage_json() -> None:
    raw = (
        "%7B%22data%22%3A%7B%22access_token%22%3A%22login%3Ademo-token%22"
        "%2C%22k%22%3A%22match-key%22%7D%7D"
    )

    imported = parse_session_request(raw)

    assert imported.token == "login:demo-token"
    assert imported.match_key == "match-key"


def test_parse_sign_only_explains_that_it_is_not_a_session() -> None:
    with pytest.raises(SessionImportError, match="SIGN"):
        parse_session_request("SIGN: a2449e96c233ea783521e365789b7f8e3a1b062a")


def test_portable_session_round_trip() -> None:
    portable = encode_portable_session(
        match_url="https://match.ichunqiu.com/2026qwbs10",
        match_key="match-key",
        token="login:portable-token",
        cookies="PHPSESSID=session-value",
    )

    imported = parse_session_request(portable)

    assert portable.startswith(PORTABLE_SESSION_PREFIX)
    assert imported.token == "login:portable-token"
    assert imported.cookies == "PHPSESSID=session-value"
    assert imported.match_key == "match-key"
    assert imported.match_url == "https://match.ichunqiu.com/2026qwbs10"


def test_portable_session_rejects_truncated_payload() -> None:
    with pytest.raises(SessionImportError, match="格式无效"):
        parse_session_request(f"{PORTABLE_SESSION_PREFIX}not-valid-base64")


def test_browser_login_script_sets_storage_and_redirects() -> None:
    script = browser_login_script(
        match_url="https://match.ichunqiu.com/demo",
        source_id="source-id",
        token="secret-token",
    )

    assert 'localStorage.setItem(sourceId, JSON.stringify(user))' in script
    assert '"token":"secret-token"' in script
    assert "location.assign(target)" in script
    assert "https://match.ichunqiu.com/demo" in script
