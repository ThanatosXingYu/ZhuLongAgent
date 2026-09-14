"""Bounded HTTP transport and authentication flows for iChunQiu matches."""

from __future__ import annotations

import json
import hashlib
import logging
import socket
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from http.cookiejar import CookieJar
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
    getproxies,
)

from .agent import APIError, PlatformNotConfigured
from .config import Config
from .ichunqiu_crypto import (
    API_ORIGIN,
    MATCH_ORIGIN,
    JsonPrimitive,
    build_request_payload,
    encrypt_login_field,
    parse_match_url,
    request_signature,
)

MAX_JSON_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CAPTCHA_BYTES = 2 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
LOGGER = logging.getLogger("gcsis-tools.ichunqiu")

SCHEMA_EXPECTATIONS: dict[str, frozenset[str]] = {
    "/match/team/info": frozenset({"team_id", "team_name"}),
    "/match/detail": frozenset({"title"}),
    "/match/sso/detail": frozenset({"title"}),
}


class ConfigProvider(Protocol):
    def __call__(self) -> Config: ...


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes
    content_type: str = ""


Transport = Callable[[Request, float, int], HTTPResponse]


@dataclass(frozen=True)
class BindResult:
    match_url: str
    match_key: str
    platform_token: str
    match: Mapping[str, Any]
    authenticated: bool


@dataclass(frozen=True)
class LoginResult:
    platform_token: str
    match_key: str


@dataclass(frozen=True)
class AuthStatus:
    authenticated: bool
    team_name: str = ""
    message: str = ""
    error_code: str = ""
    team_id: str = ""
    user_name: str = ""
    organization: str = ""
    score: float | None = None
    rank: int | None = None
    member_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "teamName": self.team_name,
            "message": self.message,
            "teamId": self.team_id,
            "userName": self.user_name,
            "organization": self.organization,
            "score": self.score,
            "rank": self.rank,
            "memberCount": self.member_count,
        }


@dataclass(frozen=True)
class CaptchaImage:
    body: bytes
    content_type: str


class IChunQiuAPI:
    """Synchronous API transport with a fixed upstream origin."""

    def __init__(
        self,
        config_provider: ConfigProvider,
        *,
        timeout: float = 15.0,
        transport: Transport | None = None,
        schema_path: str | Path | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._timeout = timeout
        self._transport = transport
        # Keep the small HTTP session cookies issued by the platform/WAF
        # between captcha, SMS and login requests, matching browser behavior.
        self._cookies = CookieJar()
        self._session_lock = threading.RLock()
        self._schema_path = Path(schema_path) if schema_path else None
        self._schema_lock = threading.RLock()
        self._schema_state: dict[str, dict[str, Any]] = {}
        self._schema_changes: list[dict[str, Any]] = []
        self._load_schema_state()

    def bind_match(self, match_url: str, supplied_token: str = "") -> BindResult:
        address = parse_match_url(match_url)
        config = self._config_provider()
        reuse_existing_context = bool(
            supplied_token.strip()
            and config.match_key.strip()
            and config.match_url.rstrip("/") == address.normalized_url.rstrip("/")
        )
        existing_match_key = config.match_key.strip()
        if reuse_existing_context:
            match = self.post(
                "/match/detail",
                {"url_key": existing_match_key, "is_encrypt": 1},
                match_key=existing_match_key,
                token=supplied_token.strip(),
            )
        elif address.encrypted_binding:
            match = self.post(
                "/match/detail",
                {"url_key": address.entry_key, "is_encrypt": 1},
                match_key=address.entry_key,
                token="",
            )
        else:
            match = self._bind_slug(address.event_slug)
        match_data = _mapping(match)
        # The official frontend keeps the original query ``k`` when the
        # address is ``/index?k=...``.  ``/match/detail`` also returns a
        # ``url_key`` field, but that value is not interchangeable with the
        # query access key and using it breaks the authenticated context.
        match_key = (
            existing_match_key
            if reuse_existing_context
            else address.entry_key
            or str(match_data.get("url_key", "") or "").strip()
        )
        if not match_key:
            raise APIError("INVALID_MATCH", "比赛接口未返回有效访问参数")

        token = supplied_token.strip()
        supplied_login = bool(token)
        if not supplied_login:
            token_data = _mapping(
                self.post("/common/account_token", {}, match_key=match_key, token="")
            )
            token = str(token_data.get("token", "") or "").strip()
        if not token:
            raise APIError("INVALID_TOKEN", "平台未返回有效登录 Token")
        # Match binding is intentionally limited to the same two calls used
        # by the official frontend.  Probing ``surplus_time`` or ``team/info``
        # with a newly-issued anonymous token can invalidate the dynamic
        # context before the captcha/login flow starts.  Validate only an
        # explicitly supplied session token, which is the reuse path.
        status = (
            self.auth_status(match_key=match_key, token=token)
            if supplied_login
            else AuthStatus(False)
        )
        return BindResult(
            match_url=address.normalized_url,
            match_key=match_key,
            platform_token=token,
            match=match_data,
            authenticated=status.authenticated,
        )

    def auth_status(
        self, *, match_key: str | None = None, token: str | None = None
    ) -> AuthStatus:
        try:
            data = _mapping(
                self.post(
                    "/match/team/info",
                    {"team_id": ""},
                    match_key=match_key,
                    token=token,
                )
            )
        except APIError as exc:
            if exc.code not in {"8", "112", "114", "116", "130", "203"}:
                raise
            return AuthStatus(False, message=exc.message, error_code=exc.code)
        team_name = _first_text(data, "team_name", "teamName", "name")
        user_name = _first_text(data, "user_name", "userName")
        return AuthStatus(
            True,
            team_name=team_name or user_name,
            error_code="",
            team_id=_first_text(data, "team_id", "teamId"),
            user_name=user_name,
            organization=_first_text(
                data,
                "school",
                "school_name",
                "company",
                "company_name",
                "organization",
            ),
            score=_optional_float(data, "total_score", "team_score", "score"),
            rank=_optional_int(data, "team_rank", "rank"),
            member_count=_optional_int(
                data, "member_count", "team_num", "user_count", "users_count"
            ),
        )

    def login_password(
        self, account: str, password: str, image_code: str = ""
    ) -> LoginResult:
        data = _mapping(
            self.post(
                "/match/login",
                {
                    "account": encrypt_login_field(account),
                    "password": encrypt_login_field(password),
                    "img_code": image_code,
                },
            )
        )
        return self._login_result(data)

    def send_sms_code(self, area_id: str, phone: str, rotation: int) -> None:
        area_id = self._resolve_sms_area_id(area_id)
        self.post(
            "/common/sms/send_code",
            {
                "area_id": area_id,
                "phone": encrypt_login_field(phone),
                "img_code": str(rotation),
            },
        )

    def _resolve_sms_area_id(self, requested: str) -> str:
        config = self._config_provider()
        requested = requested.strip()
        configured = config.match_source_id.strip()
        if configured and (not requested or requested.isdigit()):
            return configured
        if requested and not requested.isdigit():
            return requested
        try:
            address = parse_match_url(config.match_url)
            if address.event_slug:
                data = _mapping(
                    self.post(
                        "/match/sso/detail",
                        {"url_key": address.event_slug},
                        match_key="",
                        token="",
                    )
                )
                source_id = _first_text(data, "s_source_id", "s_sso_id")
                if source_id:
                    return source_id
        except (APIError, PlatformNotConfigured, ValueError, RuntimeError):
            pass
        return requested

    def login_sms(self, phone: str, sms_code: str) -> LoginResult:
        data = _mapping(
            self.post(
                "/match/p_login",
                {
                    "phone": encrypt_login_field(phone),
                    "sms_code": encrypt_login_field(sms_code),
                    "img_code": "",
                },
            )
        )
        return self._login_result(data)

    def logout(self) -> None:
        self.post("/match/logout", {})

    def captcha(self, kind: Literal["image", "rotate"]) -> CaptchaImage:
        match_key, token = self._settings()
        path = "/common/verify_old_image" if kind == "image" else "/common/verify_image"
        query = urlencode({"token": token, "time": time.time_ns() // 1_000_000})
        request = Request(f"{API_ORIGIN}{path}?{query}", method="GET")
        self._add_browser_headers(request)
        try:
            response = self._send(request, MAX_CAPTCHA_BYTES)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise RuntimeError("请求 i春秋验证码失败") from exc
        if not 200 <= response.status < 300:
            raise APIError(
                str(response.status), _http_message(response.status), response.status
            )
        LOGGER.info(
            "iChunQiu captcha path=%s status=%d k=%s token=%s",
            path,
            response.status,
            _secret_summary(match_key),
            _secret_summary(token),
        )
        content_type = response.content_type.split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise RuntimeError("i春秋返回了无效的验证码图片")
        return CaptchaImage(response.body, content_type)

    def post(
        self,
        path: str,
        values: Mapping[str, JsonPrimitive],
        *,
        match_key: str | None = None,
        token: str | None = None,
    ) -> Any:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("invalid fixed API path")
        if match_key is None or token is None:
            configured_key, configured_token = self._settings()
            match_key = configured_key if match_key is None else match_key
            token = configured_token if token is None else token
        payload = build_request_payload(values, match_key, token)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        request = Request(f"{API_ORIGIN}{path}", data=body, method="POST")
        self._add_browser_headers(request)
        request.add_header("Content-Type", "application/json;charset=UTF-8")
        request.add_header("SIGN", request_signature(payload))
        try:
            response = self._send(request, MAX_JSON_RESPONSE_BYTES)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise RuntimeError("请求 i春秋接口失败") from exc
        return self._decode_json(response, path=path, payload=payload)

    def _bind_slug(self, slug: str) -> Any:
        last_error: APIError | None = None
        try:
            return self.post(
                "/match/sso/detail",
                {"url_key": slug},
                match_key="",
                token="",
            )
        except APIError as exc:
            last_error = exc

        candidates = (slug, f"{slug}-views") if not slug.endswith("-views") else (slug,)
        for candidate in candidates:
            try:
                return self.post(
                    "/match/detail",
                    {"url_key": candidate, "is_encrypt": 2},
                    match_key="",
                    token="",
                )
            except APIError as exc:
                last_error = exc
        message = last_error.message if last_error is not None else "比赛地址无效"
        raise APIError("INVALID_MATCH", message)

    def _login_result(self, data: Mapping[str, Any]) -> LoginResult:
        token = str(data.get("token", "") or "").strip()
        if not token:
            raise APIError("INVALID_TOKEN", "登录成功响应中没有 Token")
        match_key = str(data.get("url_key", "") or "").strip()
        if not match_key:
            match_key = self._config_provider().match_key.strip()
        if not match_key:
            raise APIError("INVALID_MATCH", "登录成功响应中没有比赛访问参数")
        LOGGER.info(
            "iChunQiu login context received fields=%s k=%s token=%s",
            sorted(str(key) for key in data),
            _secret_summary(match_key),
            _secret_summary(token),
        )
        return LoginResult(token, match_key)

    def _settings(self) -> tuple[str, str]:
        config = self._config_provider()
        match_key = config.match_key.strip()
        token = config.platform_token.strip()
        if not match_key or not token:
            raise PlatformNotConfigured("比赛尚未绑定")
        return match_key, token

    def _send(self, request: Request, maximum: int) -> HTTPResponse:
        if self._transport is not None:
            response = self._transport(request, self._timeout, maximum)
            if len(response.body) > maximum:
                raise RuntimeError("i春秋响应体过大")
            return response
        try:
            with self._session_lock:
                opener = build_opener(
                    ProxyHandler(getproxies()),
                    HTTPCookieProcessor(self._cookies),
                )
                response = opener.open(request, timeout=self._timeout)  # noqa: S310 - origin is fixed above.
            with response:
                body = response.read(maximum + 1)
                if len(body) > maximum:
                    raise RuntimeError("i春秋响应体过大")
                return HTTPResponse(
                    int(response.status),
                    body,
                    str(response.headers.get("Content-Type", "")),
                )
        except HTTPError as error:
            body = error.read(maximum + 1)
            if len(body) > maximum:
                raise RuntimeError("i春秋响应体过大") from error
            return HTTPResponse(
                int(error.code),
                body,
                str(error.headers.get("Content-Type", "")),
            )

    def _decode_json(
        self,
        response: HTTPResponse,
        *,
        path: str = "",
        payload: Mapping[str, JsonPrimitive] | None = None,
    ) -> Any:
        try:
            envelope = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("i春秋返回了无效 JSON") from exc
        if not isinstance(envelope, Mapping):
            raise RuntimeError("i春秋返回了无效 JSON")
        raw_code = envelope.get("code")
        code = "" if raw_code is None else str(raw_code)
        message = str(envelope.get("message", "") or "").strip()
        if path and payload is not None:
            data = envelope.get("data")
            data_keys = sorted(str(key) for key in data.keys()) if isinstance(data, Mapping) else []
            self._observe_schema(path, data)
            LOGGER.info(
                "iChunQiu response path=%s status=%d code=%s message=%s data_keys=%s k=%s token=%s fields=%s",
                path,
                response.status,
                code,
                message[:160],
                data_keys,
                _secret_summary(str(payload.get("k", ""))),
                _secret_summary(str(payload.get("token", ""))),
                sorted(str(key) for key in payload if key not in {"k", "stamp", "token", "rs"}),
            )
        if not 200 <= response.status < 300 or code != "0":
            raise APIError(
                code, message or _http_message(response.status), response.status
            )
        return envelope.get("data")

    def schema_diagnostics(self) -> dict[str, Any]:
        with self._schema_lock:
            return {
                "changes": [dict(item) for item in self._schema_changes],
                "interfaces": {
                    path: dict(value) for path, value in self._schema_state.items()
                },
            }

    def _observe_schema(self, path: str, data: Any) -> None:
        fields = _schema_fields(data)
        if not fields:
            return
        now = datetime_now_wire()
        expected = SCHEMA_EXPECTATIONS.get(path, frozenset())
        with self._schema_lock:
            previous = self._schema_state.get(path)
            missing = sorted(expected - fields)
            added = sorted(fields - set(previous.get("fields", []))) if previous else []
            removed = sorted(set(previous.get("fields", [])) - fields) if previous else []
            state = {
                "fields": sorted(fields),
                "seenAt": now,
                "missingImportant": missing,
            }
            self._schema_state[path] = state
            if previous and (added or removed or missing):
                change = {
                    "path": path,
                    "added": added,
                    "removed": removed,
                    "missingImportant": missing,
                    "seenAt": now,
                }
                self._schema_changes = (self._schema_changes + [change])[-100:]
            self._persist_schema_state_locked()

    def _load_schema_state(self) -> None:
        if self._schema_path is None:
            return
        try:
            raw = json.loads(self._schema_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, Mapping):
            return
        interfaces = raw.get("interfaces")
        changes = raw.get("changes")
        with self._schema_lock:
            if isinstance(interfaces, Mapping):
                self._schema_state = {
                    str(path): dict(value)
                    for path, value in interfaces.items()
                    if isinstance(value, Mapping)
                }
            if isinstance(changes, list):
                self._schema_changes = [dict(item) for item in changes[-100:] if isinstance(item, Mapping)]

    def _persist_schema_state_locked(self) -> None:
        if self._schema_path is None:
            return
        try:
            self._schema_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self._schema_path.name}.", dir=str(self._schema_path.parent)
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"version": 1, "interfaces": self._schema_state, "changes": self._schema_changes},
                        handle,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    handle.write("\n")
                os.chmod(temporary, 0o600)
                os.replace(temporary, self._schema_path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("无法保存平台接口字段诊断", exc_info=True)

    @staticmethod
    def _add_browser_headers(request: Request) -> None:
        request.add_header("Accept", "application/json, text/plain, */*")
        request.add_header("Accept-Language", "zh-CN,zh;q=0.9")
        request.add_header("Origin", MATCH_ORIGIN)
        request.add_header("Referer", f"{MATCH_ORIGIN}/")
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("X-Lang", "zh-CN")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(value: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        candidate = str(value.get(key, "") or "").strip()
        if candidate:
            return candidate
    return ""


def _optional_float(value: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, (str, int, float)) and candidate != "":
            try:
                return float(candidate)
            except (TypeError, ValueError):
                continue
    return None


def _optional_int(value: Mapping[str, Any], *keys: str) -> int | None:
    number = _optional_float(value, *keys)
    return int(number) if number is not None else None


def _http_message(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return f"HTTP {status}"


def _secret_summary(value: str) -> str:
    """Return a non-reversible identifier for a token or match access key."""

    raw = value.encode("utf-8")
    return f"len={len(value)} sha256={hashlib.sha256(raw).hexdigest()[:12]}"


def _schema_fields(data: Any) -> set[str]:
    if isinstance(data, Mapping):
        return {str(key) for key in data}
    if isinstance(data, list):
        fields: set[str] = set()
        for item in data:
            if isinstance(item, Mapping):
                fields.update(str(key) for key in item)
        return fields
    return set()


def datetime_now_wire() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
