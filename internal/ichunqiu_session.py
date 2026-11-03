"""Extract reusable authentication material from copied iChunQiu requests."""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
from collections.abc import Mapping, Set
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qsl, unquote, urlsplit

from .ichunqiu_crypto import MATCH_HOST, MATCH_ORIGIN, MatchURLValidationError, parse_match_url

MAX_REQUEST_DUMP_BYTES = 64 * 1024
MAX_COOKIE_HEADER_BYTES = 8 * 1024
PORTABLE_SESSION_PREFIX = "ichunqiu-login-v1:"
_TOKEN_KEYS = frozenset(
    {"token", "access_token", "accessToken", "auth_token", "authToken"}
)
_MATCH_KEY_KEYS = frozenset({"k", "url_key", "urlKey"})
_COOKIE_TOKEN_KEYS = frozenset(
    {
        "token",
        "access_token",
        "accessToken",
        "auth_token",
        "authToken",
        "ichunqiu_token",
    }
)
_SESSION_COOKIE_NAMES = frozenset(
    {
        "phpsessid",
        "ichunqiu_token",
        "token",
        "access_token",
        "auth_token",
        "session",
        "sessionid",
        "session_id",
    }
)
_AUTH_HEADER_KEYS = frozenset(
    {"authorization", "x-token", "x-auth-token", "x-access-token"}
)
_DATA_FLAGS = frozenset(
    {
        "--data",
        "--data-raw",
        "--data-binary",
        "--data-urlencode",
        "-d",
    }
)
_HEADER_FLAGS = frozenset({"-H", "--header"})
_COOKIE_FLAGS = frozenset({"-b", "--cookie"})
_URL_FLAGS = frozenset(
    {
        "--url",
        "-X",
        "--request",
        "-A",
        "--user-agent",
        "-e",
        "--referer",
        "--connect-timeout",
        "--max-time",
        "-o",
        "--output",
        "--proxy",
        "-x",
    }
)


class SessionImportError(ValueError):
    """Raised when a pasted request does not contain a usable login state."""


@dataclass(frozen=True)
class ImportedSession:
    """Validated values extracted from one copied browser request."""

    token: str = ""
    cookies: str = ""
    match_key: str = ""
    match_url: str = ""


def encode_portable_session(
    *, match_url: str, match_key: str, token: str, cookies: str = ""
) -> str:
    payload = json.dumps(
        {
            "matchUrl": match_url.strip(),
            "matchKey": match_key.strip(),
            "token": token.strip(),
            "cookies": cookies.strip(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{PORTABLE_SESSION_PREFIX}{encoded}"


def browser_login_script(
    *, match_url: str, source_id: str, token: str
) -> str:
    user = json.dumps(
        {
            "user_id": "ichunqiu-agent-imported",
            "token": token.strip(),
            "user_name": "",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "(() => {\n"
        "  if (!/(^|\\.)ichunqiu\\.com$/i.test(location.hostname)) {\n"
        "    alert('请先打开 i春秋比赛页面，再粘贴这段代码');\n"
        "    return;\n"
        "  }\n"
        f"  const sourceId = {json.dumps(source_id.strip())};\n"
        f"  const user = {user};\n"
        f"  const target = {json.dumps(match_url.strip())};\n"
        "  localStorage.setItem(sourceId, JSON.stringify(user));\n"
        "  location.assign(target);\n"
        "})();"
    )


def parse_session_request(raw: str) -> ImportedSession:
    """Extract reusable authentication material from copied text."""

    value = raw.strip()
    if not value:
        raise SessionImportError("请粘贴带登录态的请求包")
    if len(value.encode("utf-8")) > MAX_REQUEST_DUMP_BYTES:
        raise SessionImportError("请求包过大")
    if value.startswith(PORTABLE_SESSION_PREFIX):
        imported = _parse_portable_session(value)
        if not imported.token and not imported.cookies:
            raise SessionImportError("便携登录态中没有可用凭据")
        return imported
    if value.lstrip().lower().startswith("curl "):
        request = _parse_curl(value)
    elif _looks_like_http_request(value):
        try:
            request = _parse_raw_http(value)
        except SessionImportError:
            request = _parse_loose_text(value)
    else:
        request = _parse_loose_text(value)
    cookies = _normalize_cookie_header(request.cookies)
    token = _extract_token(request)
    if not token and not cookies:
        if re.search(r"(?im)^\s*sign\s*:", value):
            raise SessionImportError(
                "SIGN 只是单次请求签名，不能作为登录态；"
                "请粘贴包含 Cookie、token 或 Authorization 的文本"
            )
        if request.content_length > 0 and not request.body:
            raise SessionImportError(
                "请求体不完整，请重新复制包含完整 Cookie 或请求体的请求包"
            )
        raise SessionImportError(
            "内容中未找到可用的 Cookie、token 或 Authorization"
        )
    match_url = _extract_match_url(request)
    match_key = _extract_match_key(request)
    if match_url and not match_key:
        match_key = _match_key_from_url(match_url)
    return ImportedSession(
        token=token,
        cookies=cookies,
        match_key=match_key,
        match_url=match_url,
    )


def _parse_portable_session(value: str) -> ImportedSession:
    encoded = value[len(PORTABLE_SESSION_PREFIX) :].strip()
    if not encoded or len(encoded) > MAX_REQUEST_DUMP_BYTES:
        raise SessionImportError("便携登录态格式无效")
    try:
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(f"{encoded}{padding}")
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise SessionImportError("便携登录态格式无效") from exc
    if not isinstance(payload, Mapping):
        raise SessionImportError("便携登录态格式无效")
    token = str(payload.get("token", "") or "").strip()
    cookies = _normalize_cookie_header(str(payload.get("cookies", "") or ""))
    match_url = str(payload.get("matchUrl", "") or "").strip()
    match_key = str(payload.get("matchKey", "") or "").strip()
    if len(token) > 4096 or len(match_key) > 512 or len(match_url) > 2048:
        raise SessionImportError("便携登录态内容过大")
    if match_url:
        try:
            match_url = parse_match_url(match_url).normalized_url
        except MatchURLValidationError as exc:
            raise SessionImportError("便携登录态中的比赛地址无效") from exc
    return ImportedSession(
        token=token,
        cookies=cookies,
        match_key=match_key,
        match_url=match_url,
    )


@dataclass(frozen=True)
class _RequestDump:
    url: str
    headers: Mapping[str, str]
    body: str
    cookies: str = ""
    content_length: int = 0
    text: str = ""


def _looks_like_http_request(value: str) -> bool:
    lines = _normalize_raw_http_lines(value.replace("\r\n", "\n").split("\n"))
    first_line = next((line.strip() for line in lines if line.strip()), "")
    return bool(
        re.match(r"^[A-Za-z]{3,16}\s+\S+\s+HTTP/\d(?:\.\d)?$", first_line)
    )


def _parse_raw_http(value: str) -> _RequestDump:
    lines = _normalize_raw_http_lines(value.replace("\r\n", "\n").split("\n"))
    request_line = lines[0].strip()
    parts = request_line.split()
    if len(parts) < 2:
        raise SessionImportError("请求包首行必须是 HTTP 请求行")
    url = _unescape_http_value(parts[1].strip())
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False
    current_header = ""
    for line in lines[1:]:
        if in_body:
            body_lines.append(line)
            continue
        if not line.strip():
            in_body = True
            continue
        if line[:1] in {" ", "\t"} and current_header:
            headers[current_header] = f"{headers[current_header]} {line.strip()}".strip()
            continue
        if ":" not in line:
            raise SessionImportError("请求头格式无效")
        name, header_value = line.split(":", 1)
        current_header = name.strip().lower()
        if not current_header:
            raise SessionImportError("请求头格式无效")
        headers[current_header] = _unescape_http_value(header_value.strip())
    content_length = _content_length(headers.get("content-length", ""))
    body = "\n".join(body_lines).strip()
    return _RequestDump(
        url=url,
        headers=headers,
        body=body,
        cookies=headers.get("cookie", ""),
        content_length=content_length,
        text=value,
    )


def _parse_loose_text(value: str) -> _RequestDump:
    """Parse headers or arbitrary text without requiring a request line."""

    normalized = _normalize_raw_http_lines(value.replace("\r\n", "\n").split("\n"))
    headers: dict[str, str] = {}
    for line in normalized:
        candidate = line.strip()
        if not candidate or ":" not in candidate:
            continue
        name, header_value = candidate.split(":", 1)
        header_name = name.strip().lower()
        if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", header_name):
            headers.setdefault(
                header_name, _unescape_http_value(header_value.strip())
            )
    urls = re.findall(r"https?://[^\s\"'<>\\]+", value)
    body = value
    json_start = value.find("{")
    json_end = value.rfind("}")
    if 0 <= json_start < json_end:
        body = value[json_start : json_end + 1]
    return _RequestDump(
        url=urls[0] if urls else "",
        headers=headers,
        body=body,
        cookies=headers.get("cookie", "") or _find_loose_cookie(value),
        content_length=_content_length(headers.get("content-length", "")),
        text=value,
    )


def _normalize_raw_http_lines(lines: list[str]) -> list[str]:
    normalized: list[str] = []
    in_body = False
    for raw_line in lines:
        line = raw_line
        if not in_body and line.rstrip().endswith("\\"):
            line = line.rstrip()[:-1].rstrip()
        if not in_body and not line.strip():
            in_body = True
        normalized.append(line)
    return normalized


def _unescape_http_value(value: str) -> str:
    return re.sub(r"\\([:/*])", r"\1", value)


def _content_length(value: str) -> int:
    try:
        length = int(value.strip())
    except ValueError:
        return 0
    return max(0, length)


def _parse_curl(value: str) -> _RequestDump:
    try:
        tokens = shlex.split(_normalize_curl_ansi_quotes(value), posix=True)
    except ValueError as exc:
        raise SessionImportError("cURL 请求格式无效") from exc
    if not tokens or tokens[0].lower() != "curl":
        raise SessionImportError("未找到 cURL 请求")
    url = ""
    headers: dict[str, str] = {}
    body = ""
    cookies = ""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in _HEADER_FLAGS:
            index += 1
            if index >= len(tokens) or ":" not in tokens[index]:
                raise SessionImportError("cURL 请求头格式无效")
            name, header_value = tokens[index].split(":", 1)
            header_name = name.strip().lower()
            if header_name:
                headers[header_name] = header_value.strip()
        elif token in _COOKIE_FLAGS:
            index += 1
            if index >= len(tokens):
                raise SessionImportError("cURL Cookie 参数无效")
            cookies = tokens[index]
        elif token in _DATA_FLAGS:
            index += 1
            if index >= len(tokens):
                raise SessionImportError("cURL 请求体参数无效")
            body = tokens[index]
        elif token in _URL_FLAGS:
            index += 1
        elif token.startswith("http://") or token.startswith("https://"):
            url = token
        index += 1
    cookies = cookies or headers.get("cookie", "")
    if not url:
        raise SessionImportError("请求包中未找到接口地址")
    return _RequestDump(url=url, headers=headers, body=body, cookies=cookies)


def _normalize_curl_ansi_quotes(value: str) -> str:
    # Chrome's "Copy as cURL" may use Bash $'...' strings. shlex in POSIX mode
    # does not understand the leading dollar sign, so decode the small subset
    # of escapes commonly present in request bodies.
    pattern = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.DOTALL)

    def replace(match: re.Match[str]) -> str:
        escaped = match.group(1)
        escaped = escaped.replace("\\'", "'").replace('\\"', '"')
        escaped = escaped.replace("\\n", "\n").replace("\\r", "\r")
        escaped = escaped.replace("\\t", "\t").replace("\\\\", "\\")
        return shlex.quote(escaped)

    return pattern.sub(replace, value)


def _normalize_cookie_header(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if len(raw.encode("utf-8")) > MAX_COOKIE_HEADER_BYTES:
        raise SessionImportError("Cookie 内容过大")
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except CookieError as exc:
        raise SessionImportError("Cookie 格式无效") from exc
    pairs = [f"{name}={morsel.coded_value}" for name, morsel in cookie.items()]
    if not pairs:
        raise SessionImportError("Cookie 格式无效")
    normalized = "; ".join(pairs)
    if len(normalized.encode("utf-8")) > MAX_COOKIE_HEADER_BYTES:
        raise SessionImportError("Cookie 内容过大")
    return normalized


def _find_loose_cookie(value: str) -> str:
    header_match = re.search(r"(?im)^\s*cookie\s*:\s*(.+)$", value)
    if header_match:
        return header_match.group(1).strip()
    pair_pattern = re.compile(
        r"(?i)(?:^|[;\s])([A-Za-z0-9_.-]+)=([^;\s\"'<>]+)"
    )
    pairs: list[str] = []
    seen: set[str] = set()
    for match in pair_pattern.finditer(value):
        name = match.group(1)
        if name.lower() not in _SESSION_COOKIE_NAMES or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={match.group(2)}")
    return "; ".join(pairs)


def _extract_token(request: _RequestDump) -> str:
    payload = _parse_body(request.body)
    token = _first_nested_text(payload, _TOKEN_KEYS)
    if token:
        return token
    query = _parse_url_values(request.url)
    token = _find_text(query, _TOKEN_KEYS)
    if token:
        return token
    for name, value in request.headers.items():
        if name in _AUTH_HEADER_KEYS:
            candidate = value.strip()
            if candidate.lower().startswith("bearer "):
                candidate = candidate[7:].strip()
            if candidate:
                return candidate
    cookie = SimpleCookie()
    try:
        cookie.load(request.cookies)
    except CookieError:
        return ""
    for name, morsel in cookie.items():
        if name in _COOKIE_TOKEN_KEYS and morsel.value.strip():
            return morsel.value.strip()
    token = _find_token_in_text(request.text)
    if token:
        return token
    return ""


def _find_token_in_text(value: str) -> str:
    patterns = (
        re.compile(
            r"(?i)(?:^|[^A-Za-z0-9_])(?:token|access_token|auth_token)"
            r"\s*[:=]\s*[\"']?([A-Za-z0-9._~+/:%=+-]{8,4096})"
        ),
        re.compile(r"\blogin:[A-Za-z0-9._~+/:%=+-]{8,4096}"),
    )
    for pattern in patterns:
        match = pattern.search(value)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return ""


def _extract_match_key(request: _RequestDump) -> str:
    payload = _parse_body(request.body)
    match_key = _first_nested_text(payload, _MATCH_KEY_KEYS)
    if match_key:
        return match_key
    query = _parse_url_values(request.url)
    match_key = _find_text(query, _MATCH_KEY_KEYS)
    if match_key:
        return match_key
    pattern = re.compile(
        r"(?i)(?:^|[?&;,\s])(?:k|url_key|urlKey|\"k\"|\"url_key\")"
        r"\s*[:=]\s*[\"']?([A-Za-z0-9_-]{4,512})"
    )
    match = pattern.search(request.text)
    return match.group(1) if match else ""


def _extract_match_url(request: _RequestDump) -> str:
    candidates = [
        request.headers.get("referer", ""),
        request.headers.get("origin", ""),
        request.url,
    ]
    for candidate in candidates:
        normalized = _normalize_match_candidate(candidate)
        if normalized:
            return normalized
    payload = _parse_body(request.body)
    for key in ("match_url", "matchUrl", "url"):
        candidate = _first_nested_text(payload, {key})
        normalized = _normalize_match_candidate(candidate)
        if normalized:
            return normalized
    for candidate in re.findall(r"https?://[^\s\"'<>\\]+", request.text):
        normalized = _normalize_match_candidate(candidate)
        if normalized:
            return normalized
    return ""


def _normalize_match_candidate(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or parsed.hostname != MATCH_HOST:
        return ""
    path = unquote(parsed.path).strip("/")
    if not path:
        return ""
    if parsed.query:
        candidate = f"{MATCH_ORIGIN}/{path}?{parsed.query}"
    else:
        candidate = f"{MATCH_ORIGIN}/{path.split('/', 1)[0]}"
    try:
        return parse_match_url(candidate).normalized_url
    except MatchURLValidationError:
        return ""


def _match_key_from_url(value: str) -> str:
    try:
        return parse_match_url(value).entry_key
    except MatchURLValidationError:
        return ""


def _parse_body(body: str) -> Mapping[str, object]:
    raw = body.strip()
    if not raw:
        return {}
    value: object = None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        pass
    if isinstance(value, str):
        nested = value.strip()
        try:
            value = json.loads(nested)
        except json.JSONDecodeError:
            value = {}
    if not isinstance(value, Mapping) and "%" in raw:
        decoded = unquote(raw).strip()
        try:
            value = json.loads(decoded)
        except json.JSONDecodeError:
            value = {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    try:
        form = dict(parse_qsl(raw, keep_blank_values=True))
    except ValueError:
        form = {}
    return form


def _parse_url_values(value: str) -> Mapping[str, object]:
    try:
        return dict(parse_qsl(urlsplit(value).query, keep_blank_values=True))
    except ValueError:
        return {}


def _find_text(value: object, keys: Set[str]) -> str:
    if not isinstance(value, Mapping):
        return ""
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _first_nested_text(value: object, keys: Set[str], depth: int = 0) -> str:
    if depth > 2:
        return ""
    direct = _find_text(value, keys)
    if direct:
        return direct
    if isinstance(value, Mapping):
        for item in value.values():
            found = _first_nested_text(item, keys, depth + 1)
            if found:
                return found
    return ""
