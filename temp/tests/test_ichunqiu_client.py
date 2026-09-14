from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request

import pytest
from fastapi.testclient import TestClient

import internal.ichunqiu_api as ichunqiu_api_module
from internal.agent import APIError
from internal.config import Config, RuntimeConfigStore
from internal.ichunqiu import AgentClient
from internal.ichunqiu_api import (
    AuthStatus,
    BindResult,
    CaptchaImage,
    HTTPResponse,
    IChunQiuAPI,
    LoginResult,
)
from internal.ichunqiu_crypto import API_ORIGIN, request_signature
from internal.web import AgentProtocol, create_app


def _json_response(value: dict[str, Any], status: int = 200) -> HTTPResponse:
    return HTTPResponse(status, json.dumps(value).encode("utf-8"), "application/json")


def _payload(request: Request) -> dict[str, Any]:
    assert isinstance(request.data, bytes)
    value = json.loads(request.data)
    assert isinstance(value, dict)
    return value


def _assert_signed_request(request: Request, payload: dict[str, Any]) -> None:
    assert request.full_url.startswith(API_ORIGIN)
    assert request.get_header("Sign") == request_signature(payload)
    assert {"k", "stamp", "token", "rs"}.issubset(payload)


def test_api_binds_slug_with_sso_detail_and_anonymous_token() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        path = urlsplit(request.full_url).path
        payload = _payload(request)
        _assert_signed_request(request, payload)
        calls.append((path, payload))
        if path == "/match/sso/detail":
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"url_key": "resolved-match-key", "title": "演示比赛"},
                }
            )
        if path == "/common/account_token":
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"token": "anonymous:not-a-real-token"},
                }
            )
        if path == "/match/surplus_time":
            return _json_response({"code": 203, "message": "比赛已结束", "data": None})
        if path == "/match/team/info":
            return _json_response(
                {"code": 116, "message": "暂无队伍信息", "data": None}
            )
        raise AssertionError(path)

    api = IChunQiuAPI(lambda: Config(), transport=transport)
    result = api.bind_match("https://match.ichunqiu.com/demo-match")
    assert result.match_key == "resolved-match-key"
    assert result.platform_token == "anonymous:not-a-real-token"
    assert result.authenticated is False
    assert calls[0][0] == "/match/sso/detail"
    assert calls[0][1]["url_key"] == "demo-match"


def test_api_reloads_system_proxy_without_losing_session_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_states = iter(
        (
            {"https": "http://127.0.0.1:18080"},
            {},
        )
    )
    proxy_handlers: list[dict[str, str]] = []

    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self, _maximum: int) -> bytes:
            return b'{"code":0,"message":"","data":{}}'

    class Opener:
        def open(self, _request: Request, *, timeout: float) -> Response:
            assert timeout == 15.0
            return Response()

    def build(*handlers: Any) -> Opener:
        proxy = next(
            handler for handler in handlers if isinstance(handler, ProxyHandler)
        )
        proxy_handlers.append(dict(proxy.proxies))
        return Opener()

    monkeypatch.setattr(ichunqiu_api_module, "getproxies", lambda: next(proxy_states))
    monkeypatch.setattr(ichunqiu_api_module, "build_opener", build)
    api = IChunQiuAPI(lambda: Config())

    api.post("/match/detail", {}, match_key="key", token="token")
    api.post("/match/detail", {}, match_key="key", token="token")

    assert proxy_handlers == [{"https": "http://127.0.0.1:18080"}, {}]


def test_api_falls_back_to_standard_match_detail_for_non_sso_slug() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        path = urlsplit(request.full_url).path
        payload = _payload(request)
        _assert_signed_request(request, payload)
        calls.append((path, payload))
        if path == "/match/sso/detail":
            return _json_response(
                {"code": 116, "message": "暂无比赛信息", "data": None}
            )
        if path == "/match/detail" and payload["url_key"] == "demo-match":
            return _json_response({"code": 101, "message": "not found", "data": None})
        if path == "/match/detail":
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"url_key": "resolved-match-key", "title": "演示比赛"},
                }
            )
        if path == "/common/account_token":
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"token": "anonymous:not-a-real-token"},
                }
            )
        if path == "/match/surplus_time":
            return _json_response(
                {"code": 8, "message": "未定义数组索引: s_source_id", "data": None}
            )
        if path == "/match/team/info":
            return _json_response(
                {"code": 8, "message": "未定义数组索引: s_source_id", "data": None}
            )
        raise AssertionError(path)

    api = IChunQiuAPI(lambda: Config(), transport=transport)
    result = api.bind_match("https://match.ichunqiu.com/demo-match")

    assert result.match_key == "resolved-match-key"
    detail_values = [
        payload["url_key"] for path, payload in calls if path == "/match/detail"
    ]
    assert detail_values == ["demo-match", "demo-match-views"]


def test_api_keeps_encrypted_entry_key_for_authenticated_requests() -> None:
    entry_key = "UCAH-test-entry-key"
    calls: list[tuple[str, dict[str, Any]]] = []

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        path = urlsplit(request.full_url).path
        payload = _payload(request)
        _assert_signed_request(request, payload)
        calls.append((path, payload))
        if path == "/match/detail":
            assert payload["url_key"] == entry_key
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"url_key": "server-generated-key", "title": "演示比赛"},
                }
            )
        if path == "/common/account_token":
            assert payload["k"] == entry_key
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"token": "anonymous:not-a-real-token"},
                }
            )
        if path == "/match/surplus_time":
            assert payload["k"] == entry_key
            return _json_response({"code": 203, "message": "比赛已结束", "data": None})
        if path == "/match/team/info":
            return _json_response(
                {"code": 116, "message": "暂无队伍信息", "data": None}
            )
        raise AssertionError(path)

    result = IChunQiuAPI(lambda: Config(), transport=transport).bind_match(
        f"https://match.ichunqiu.com/index?k={entry_key}"
    )

    assert result.match_key == entry_key
    assert calls[0][1]["k"] == entry_key


def test_api_encrypts_login_fields_and_proxies_only_fixed_captcha_path() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/demo-match",
        match_key="resolved-match-key",
        platform_token="anonymous:not-a-real-token",
    )
    captured: dict[str, Any] = {}

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        path = urlsplit(request.full_url).path
        if path == "/common/verify_image":
            return HTTPResponse(200, b"fake-image", "image/png")
        payload = _payload(request)
        _assert_signed_request(request, payload)
        captured.update(payload)
        return _json_response(
            {
                "code": 0,
                "message": "",
                "data": {
                    "token": "login:not-a-real-token",
                    "url_key": "authenticated-match-key",
                },
            }
        )

    api = IChunQiuAPI(lambda: config, transport=transport)
    login = api.login_password("demo-account", "password123")
    assert login == LoginResult("login:not-a-real-token", "authenticated-match-key")
    assert captured["account"] == "CNApD92V5OkjpA-c0ok3hw"
    assert captured["password"] == "vVRhIPfcDM3_avGchWcaqw"
    assert "demo-account" not in json.dumps(captured)
    image = api.captcha("rotate")
    assert image == CaptchaImage(b"fake-image", "image/png")


def test_api_uses_match_source_id_for_sms_area() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/demo-match",
        match_key="resolved-match-key",
        platform_token="anonymous:not-a-real-token",
        match_source_id="Gk_hVuLTvTg",
    )
    captured: dict[str, Any] = {}

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        assert urlsplit(request.full_url).path == "/common/sms/send_code"
        payload = _payload(request)
        _assert_signed_request(request, payload)
        captured.update(payload)
        return _json_response({"code": 0, "message": "", "data": None})

    IChunQiuAPI(lambda: config, transport=transport).send_sms_code(
        "86", "13800138000", 180
    )

    assert captured["area_id"] == "Gk_hVuLTvTg"


def test_api_logout_uses_authenticated_match_context() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/demo-match",
        match_key="resolved-match-key",
        platform_token="login:not-a-real-token",
    )
    captured: dict[str, Any] = {}

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        assert urlsplit(request.full_url).path == "/match/logout"
        payload = _payload(request)
        _assert_signed_request(request, payload)
        captured.update(payload)
        return _json_response({"code": 0, "message": "", "data": None})

    IChunQiuAPI(lambda: config, transport=transport).logout()

    assert captured["k"] == "resolved-match-key"
    assert captured["token"] == "login:not-a-real-token"


def test_api_keeps_login_state_when_team_info_lacks_source_context() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/demo-match",
        match_key="resolved-match-key",
        platform_token="login:match:not-a-real-token",
    )

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        assert urlsplit(request.full_url).path == "/match/team/info"
        payload = _payload(request)
        _assert_signed_request(request, payload)
        return _json_response(
            {"code": 8, "message": "未定义数组索引: s_source_id", "data": None}
        )

    status = IChunQiuAPI(lambda: config, transport=transport).auth_status()

    assert status.authenticated is False
    assert status.message == "未定义数组索引: s_source_id"
    assert status.error_code == "8"


def test_api_maps_authenticated_team_information() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/demo-match",
        match_key="resolved-match-key",
        platform_token="login:not-a-real-token",
    )

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        payload = _payload(request)
        _assert_signed_request(request, payload)
        assert urlsplit(request.full_url).path == "/match/team/info"
        return _json_response(
            {
                "code": 0,
                "message": "",
                "data": {
                    "team_id": "team-demo-id",
                    "team_name": "演示队伍",
                    "user_name": "队员甲",
                    "school_name": "演示单位",
                    "total_score": "256.5",
                    "team_rank": "7",
                    "user_count": 3,
                },
            }
        )

    status = IChunQiuAPI(lambda: config, transport=transport).auth_status()

    assert status.to_dict() == {
        "authenticated": True,
        "teamName": "演示队伍",
        "message": "",
        "teamId": "team-demo-id",
        "userName": "队员甲",
        "organization": "演示单位",
        "score": 256.5,
        "rank": 7,
        "memberCount": 3,
    }


def test_agent_maps_questions_flags_notices_and_environments() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/demo-match",
        match_key="resolved-match-key",
        platform_token="login:not-a-real-token",
    )
    questions = [
        {
            "id": "question-platform-a",
            "title": "Web Demo",
            "content": "<p>demo</p>",
            "category_id": "web",
            "category": "Web",
            "type_id": 1,
            "difficult": 2,
            "score": 100,
            "sort": 2,
            "is_allow_solve": True,
            "is_lock": False,
            "red_image": "demo-image",
            "file_list": [{"url": "https://files.example.test/a.zip", "name": "a.zip"}],
            "red_solved": {"is_solved": False},
        },
        {
            "id": "question-platform-b",
            "title": "Crypto Demo",
            "content": (
                '<p>cipher</p><p><a href="https://files.example.test/crypto.zip" '
                'target="_blank">附件下载</a></p>'
            ),
            "category_id": "crypto",
            "category": "Crypto",
            "type_id": 1,
            "score": 200,
            "is_allow_solve": True,
            "file_url": "",
            "file_list": [],
            "red_solved": {"is_solved": True},
        },
    ]
    list_calls = 0
    submitted: dict[str, Any] = {}
    opened: list[str] = []
    process_calls = 0
    closed: dict[str, Any] = {}

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        nonlocal list_calls, process_calls
        path = urlsplit(request.full_url).path
        payload = _payload(request)
        _assert_signed_request(request, payload)
        if path == "/match/question/lists":
            list_calls += 1
            rows = questions if list_calls == 1 else list(reversed(questions))
            return _json_response(
                {"code": 0, "message": "", "data": {"total": 2, "lists": rows}}
            )
        if path == "/match/docker/info":
            if process_calls == 0:
                return _json_response(
                    {"code": 101, "message": "not running", "data": None}
                )
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {
                        "uuid": "environment-demo-id",
                        "countdown": 300,
                        "delay_count": 1,
                        "url": "https://target.example.test",
                        "ssh": {
                            "ssh_url": "ssh://target.example.test:2222",
                            "username": "ctf",
                            "password": "temporary-password",
                        },
                    },
                }
            )
        if path == "/match/red/submit":
            submitted.update(payload)
            return _json_response({"code": 0, "message": "correct", "data": None})
        if path == "/match/question/open_record":
            opened.append(str(payload["question_id"]))
            return _json_response({"code": 203, "message": "比赛已结束", "data": None})
        if path == "/match/team/info":
            return _json_response(
                {"code": 0, "message": "", "data": {"total_score": 300, "team_rank": 7}}
            )
        if path == "/match/notice/lists":
            assert payload["type"] == 1
            assert payload["page_index"] == 1
            assert payload["page_size"] == ""
            return _json_response({"code": 116, "message": "暂无数据", "data": None})
        if path == "/match/docker/apply":
            assert payload["question_id"] == "question-platform-a"
            return _json_response(
                {"code": 0, "message": "", "data": {"uuid": "environment-demo-id"}}
            )
        if path == "/match/docker/process":
            process_calls += 1
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"process": 0 if process_calls == 1 else 1},
                }
            )
        if path == "/match/docker/close":
            closed.update(payload)
            return _json_response({"code": 0, "message": "", "data": None})
        raise AssertionError(path)

    client = AgentClient(config_provider=lambda: config, transport=transport)
    first_groups = client.exercises()
    first_ids = {item.name: item.id for group in first_groups for item in group.corpus}
    first_summaries = {
        item.name: item for group in first_groups for item in group.corpus
    }
    second_groups = client.exercises(True)
    second_ids = {
        item.name: item.id for group in second_groups for item in group.corpus
    }
    assert first_ids == second_ids == {"Web Demo": 1, "Crypto Demo": 2}
    assert first_summaries["Web Demo"].has_attachment is True
    assert first_summaries["Web Demo"].has_environment is True
    assert first_summaries["Crypto Demo"].has_attachment is True
    assert first_summaries["Crypto Demo"].has_environment is False

    web = client.exercise(1)
    assert web.is_need_init is True
    assert web.category == "Web"
    assert web.attachment.files[0].name == "a.zip"
    attachment_only = client.attachment_detail(1)
    assert attachment_only.category == "Web"
    assert attachment_only.endpoints == ()
    crypto = client.exercise(2)
    assert crypto.endpoint_type == ""
    assert len(crypto.attachment.files) == 1
    assert crypto.attachment.files[0].name == "crypto.zip"
    assert crypto.attachment.files[0].url == "https://files.example.test/crypto.zip"
    assert client.overview().to_dict() == {"stagePoint": 300.0, "stageRank": 7}
    assert client.notices() == []

    assert client.submit_flag(1, "flag{demo}").is_correct is True
    assert submitted["question_id"] == "question-platform-a"
    assert submitted["answer"] == "flag{demo}"
    assert submitted["img_url"] == "f97fae4e1ffdb1ee3b65231aa693ea85"
    assert opened == ["question-platform-a"]

    client.build_environment(1)
    assert opened == ["question-platform-a"]
    pending = client.exercise(1)
    assert pending.is_need_check is True and pending.endpoints == ()
    ready = client.exercise(1)
    assert ready.is_need_check is False and len(ready.endpoints) == 1
    assert ready.endpoints[0].users[0].username == "ctf"
    client.recover_environment(1)
    assert closed["uuid"] == "environment-demo-id"
    assert closed["close_type"] == 3


def test_agent_maps_match_lifecycle_and_real_notice_shape() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/demo-match",
        match_key="resolved-match-key",
        platform_token="login:not-a-real-token",
    )

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        path = urlsplit(request.full_url).path
        payload = _payload(request)
        _assert_signed_request(request, payload)
        if path == "/match/sso/detail":
            return _json_response(
                {"code": 116, "message": "暂无比赛信息", "data": None}
            )
        if path == "/match/detail":
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {
                        "title": "演示比赛",
                        "status": 1,
                        "entry_type": 2,
                        "login_type": 4,
                        "start_time": "2026-09-04 13:00:00",
                        "end_time": "2026-09-04 17:00:00",
                    },
                }
            )
        if path == "/match/surplus_time":
            return _json_response({"code": 203, "message": "比赛已结束", "data": None})
        if path == "/match/notice/lists":
            assert payload["type"] == 1
            assert payload["page_index"] == 1
            assert payload["page_size"] == ""
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {
                        "total": 1,
                        "lists": [
                            {
                                "id": "notice-platform-a",
                                "title": "",
                                "content": "比赛公告正文",
                                "create_time": "2026-09-04 17:21:48",
                                "file_name": "notice.txt",
                                "file_url": "https://files.example.test/notice.txt",
                            }
                        ],
                    },
                }
            )
        raise AssertionError(path)

    client = AgentClient(config_provider=lambda: config, transport=transport)
    match_info = client.match_info()
    assert "比赛状态：已结束" in match_info.rule
    assert "比赛状态：未开始" not in match_info.rule

    notices = client.notices()
    assert len(notices) == 1
    assert notices[0].title == "比赛公告正文"
    assert notices[0].created_at == "2026-09-04 17:21:48"
    assert notices[0].created_time > 0
    detail = client.notice(notices[0].id)
    assert detail.created_time == notices[0].created_time
    assert detail.file.files[0].name == "notice.txt"


def test_agent_loads_match_info_for_sso_slug() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/wanqubei",
        match_key="resolved-match-key",
        platform_token="anonymous:not-a-real-token",
    )

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        payload = _payload(request)
        _assert_signed_request(request, payload)
        assert urlsplit(request.full_url).path == "/match/sso/detail"
        assert payload["url_key"] == "wanqubei"
        assert payload["k"] == ""
        assert payload["token"] == ""
        return _json_response(
            {
                "code": 0,
                "message": "",
                "data": {
                    "title": "第二届湾区杯",
                    "login_type": 4,
                    "match_status": 203,
                },
            }
        )

    info = AgentClient(config_provider=lambda: config, transport=transport).match_info()

    assert info.note == "第二届湾区杯"
    assert "比赛状态：已结束" in info.rule
    assert "登录方式：平台账号" in info.rule


def test_agent_uses_bound_detail_for_logged_in_sso_slug() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/wanqubei",
        match_key="resolved-match-key",
        platform_token="login:not-a-real-token",
    )

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        payload = _payload(request)
        _assert_signed_request(request, payload)
        assert urlsplit(request.full_url).path == "/match/detail"
        assert payload["url_key"] == "resolved-match-key"
        assert payload["k"] == "resolved-match-key"
        assert payload["token"] == "login:not-a-real-token"
        return _json_response(
            {
                "code": 0,
                "message": "",
                "data": {
                    "title": "第二届湾区杯",
                    "login_type": 4,
                    "match_status": 203,
                },
            }
        )

    info = AgentClient(config_provider=lambda: config, transport=transport).match_info()

    assert info.note == "第二届湾区杯"
    assert "比赛状态：已结束" in info.rule


def test_api_reuses_existing_context_for_logged_in_rebind() -> None:
    config = Config(
        match_url="https://match.ichunqiu.com/wanqubei",
        match_key="resolved-match-key",
        platform_token="login:not-a-real-token",
    )
    calls: list[str] = []

    def transport(request: Request, _timeout: float, _maximum: int) -> HTTPResponse:
        path = urlsplit(request.full_url).path
        payload = _payload(request)
        _assert_signed_request(request, payload)
        calls.append(path)
        assert payload["k"] == "resolved-match-key"
        assert payload["token"] == "login:not-a-real-token"
        if path == "/match/detail":
            assert payload["url_key"] == "resolved-match-key"
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"title": "演示比赛", "url_key": "new-server-key"},
                }
            )
        if path == "/match/team/info":
            return _json_response(
                {
                    "code": 0,
                    "message": "",
                    "data": {"team_name": "演示队伍", "team_rank": 1},
                }
            )
        raise AssertionError(path)

    result = IChunQiuAPI(lambda: config, transport=transport).bind_match(
        config.match_url, config.platform_token
    )

    assert result.match_key == "resolved-match-key"
    assert result.authenticated is True
    assert calls == ["/match/detail", "/match/team/info"]


class PlatformStub:
    def __init__(self) -> None:
        self.invalidations = 0
        self.sms_rotation = -1
        self.bind_tokens: list[str] = []
        self.logout_calls = 0

    def bind_match(self, _match_url: str, supplied_token: str = "") -> BindResult:
        self.bind_tokens.append(supplied_token)
        return BindResult(
            "https://match.ichunqiu.com/demo-match",
            "resolved-match-key",
            supplied_token or "anonymous:not-a-real-token",
            {"title": "演示比赛", "login_type": 4},
            bool(supplied_token),
        )

    def auth_status(self) -> AuthStatus:
        return AuthStatus(True, "演示队伍")

    def login_password(
        self, _account: str, _password: str, _image_code: str = ""
    ) -> LoginResult:
        return LoginResult("login:password:not-a-real-token", "password-match-key")

    def send_sms_code(self, _area_id: str, _phone: str, rotation: int) -> None:
        self.sms_rotation = rotation

    def login_sms(self, _phone: str, _sms_code: str) -> LoginResult:
        return LoginResult("login:sms:not-a-real-token", "sms-match-key")

    def logout(self) -> None:
        self.logout_calls += 1

    def captcha(self, _kind: Literal["image", "rotate"]) -> CaptchaImage:
        return CaptchaImage(b"image", "image/png")

    def invalidate(self) -> None:
        self.invalidations += 1


def test_platform_web_flows_persist_but_never_return_tokens(tmp_path: Path) -> None:
    source = PlatformStub()
    store = RuntimeConfigStore(Config(), tmp_path / ".runtime-config.json")
    app = create_app(cast(AgentProtocol, source), config_store=store)
    action = {"X-GCSIS-Action": "platform-auth"}
    with TestClient(app) as client:
        rejected = client.post(
            "/api/platform/bind",
            json={"matchUrl": "https://match.ichunqiu.com/demo-match"},
        )
        assert rejected.status_code == 403

        bound = client.post(
            "/api/platform/bind",
            headers=action,
            json={
                "matchUrl": "https://match.ichunqiu.com/demo-match",
                "platformToken": "login:supplied:not-a-real-token",
            },
        )
        assert bound.status_code == 200
        assert bound.json()["data"]["authenticated"] is True
        assert bound.json()["data"]["matchTitle"] == "演示比赛"
        assert "resolved-match-key" not in bound.text
        assert "login:supplied:not-a-real-token" not in bound.text

        rebound = client.post(
            "/api/platform/bind",
            headers=action,
            json={"matchUrl": "https://match.ichunqiu.com/demo-match"},
        )
        assert rebound.status_code == 200
        assert source.bind_tokens[-1] == "login:supplied:not-a-real-token"

        auth = client.get("/api/platform/auth")
        assert auth.json()["data"]["teamName"] == "演示队伍"
        captcha = client.get("/api/platform/captcha?kind=rotate")
        assert captcha.status_code == 200
        assert captcha.headers["cache-control"] == "no-store"

        password = client.post(
            "/api/platform/login/password",
            headers=action,
            json={
                "account": "demo",
                "password": "not-a-real-password",
                "imageCode": "",
            },
        )
        assert password.status_code == 200
        assert "login:password:not-a-real-token" not in password.text

        sent = client.post(
            "/api/platform/login/sms/send",
            headers=action,
            json={"areaId": "86", "phone": "13800138000", "rotation": 127},
        )
        assert sent.status_code == 200 and source.sms_rotation == 127
        sms = client.post(
            "/api/platform/login/sms",
            headers=action,
            json={"phone": "13800138000", "smsCode": "123456"},
        )
        assert sms.status_code == 200
        assert "login:sms:not-a-real-token" not in sms.text

    persisted = json.loads(
        (tmp_path / ".runtime-config.json").read_text(encoding="utf-8")
    )
    assert persisted["platform_token"] == "login:sms:not-a-real-token"
    assert persisted["match_key"] == "sms-match-key"
    assert persisted["match_title"] == "演示比赛"
    assert persisted["match_login_type"] == 4
    assert source.invalidations == 5


def test_platform_logout_replaces_login_with_anonymous_context(tmp_path: Path) -> None:
    source = PlatformStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="logged-in-match-key",
            platform_token="login:current:not-a-real-token",
            match_title="演示比赛",
            match_login_type=4,
            match_source_id="demo-source",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    action = {"X-GCSIS-Action": "platform-auth"}
    with TestClient(app) as client:
        rejected = client.post("/api/platform/logout")
        assert rejected.status_code == 403

        response = client.post("/api/platform/logout", headers=action)

    assert response.status_code == 200
    assert response.json()["data"]["configured"] is True
    assert response.json()["data"]["authenticated"] is False
    assert source.logout_calls == 1
    assert source.bind_tokens == [""]
    assert store.get().platform_token == "anonymous:not-a-real-token"
    assert "current:not-a-real-token" not in response.text


def test_platform_logout_clears_binding_when_anonymous_rebind_fails(
    tmp_path: Path,
) -> None:
    class FailedRebindStub(PlatformStub):
        def bind_match(self, _match_url: str, supplied_token: str = "") -> BindResult:
            self.bind_tokens.append(supplied_token)
            raise RuntimeError("anonymous rebind failed")

    source = FailedRebindStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="logged-in-match-key",
            platform_token="login:current:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/logout",
            headers={"X-GCSIS-Action": "platform-auth"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["configured"] is False
    assert source.logout_calls == 1
    assert store.get().match_url == ""
    assert store.get().match_key == ""
    assert store.get().platform_token == ""


def test_platform_logout_recovers_when_upstream_session_is_expired(
    tmp_path: Path,
) -> None:
    class ExpiredLogoutStub(PlatformStub):
        def logout(self) -> None:
            self.logout_calls += 1
            raise APIError("114", "请登录平台")

    source = ExpiredLogoutStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="logged-in-match-key",
            platform_token="login:expired:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/logout",
            headers={"X-GCSIS-Action": "platform-auth"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["configured"] is True
    assert source.logout_calls == 1
    assert store.get().platform_token == "anonymous:not-a-real-token"


def test_platform_captcha_refreshes_stale_login_context(tmp_path: Path) -> None:
    source = PlatformStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="old-match-key",
            platform_token="login:stale:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.get("/api/platform/captcha?kind=rotate")

    assert response.status_code == 200
    assert response.content == b"image"
    assert source.bind_tokens == [""]
    assert store.get().platform_token == "anonymous:not-a-real-token"


def test_platform_captcha_refreshes_anonymous_match_context(tmp_path: Path) -> None:
    source = PlatformStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="old-match-key",
            platform_token="anonymous:stale:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.get("/api/platform/captcha?kind=rotate")

    assert response.status_code == 200
    assert response.content == b"image"
    assert source.bind_tokens == [""]
    assert store.get().platform_token == "anonymous:not-a-real-token"


def test_platform_bind_recovers_from_an_invalid_saved_session(tmp_path: Path) -> None:
    class ExpiredSessionStub(PlatformStub):
        def bind_match(self, _match_url: str, supplied_token: str = "") -> BindResult:
            self.bind_tokens.append(supplied_token)
            if supplied_token:
                raise RuntimeError("expired saved session")
            return BindResult(
                "https://match.ichunqiu.com/demo-match",
                "resolved-match-key",
                "anonymous:not-a-real-token",
                {"title": "演示比赛", "login_type": 4},
                False,
            )

    source = ExpiredSessionStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="old-match-key",
            platform_token="login:expired:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/bind",
            headers={"X-GCSIS-Action": "platform-auth"},
            json={"matchUrl": "https://match.ichunqiu.com/demo-match"},
        )

    assert response.status_code == 200
    assert source.bind_tokens == ["login:expired:not-a-real-token", ""]
    assert store.get().platform_token == "anonymous:not-a-real-token"
    assert "expired:not-a-real-token" not in response.text


def test_platform_bind_recovers_when_saved_login_has_no_team_context(
    tmp_path: Path,
) -> None:
    class MissingContextStub(PlatformStub):
        def bind_match(self, _match_url: str, supplied_token: str = "") -> BindResult:
            self.bind_tokens.append(supplied_token)
            if supplied_token:
                return BindResult(
                    "https://match.ichunqiu.com/demo-match",
                    "resolved-match-key",
                    supplied_token,
                    {"title": "演示比赛", "login_type": 4},
                    False,
                )
            return BindResult(
                "https://match.ichunqiu.com/demo-match",
                "resolved-match-key",
                "not_login:not-a-real-token",
                {"title": "演示比赛", "login_type": 4},
                False,
            )

    source = MissingContextStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="old-match-key",
            platform_token="login:expired:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/bind",
            headers={"X-GCSIS-Action": "platform-auth"},
            json={"matchUrl": "https://match.ichunqiu.com/demo-match"},
        )

    assert response.status_code == 200
    assert source.bind_tokens == [
        "login:expired:not-a-real-token",
        "",
    ]
    assert store.get().platform_token == "not_login:not-a-real-token"


def test_platform_bind_does_not_reuse_saved_anonymous_token(
    tmp_path: Path,
) -> None:
    source = PlatformStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="old-match-key",
            platform_token="not_login:stale:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/bind",
            headers={"X-GCSIS-Action": "platform-auth"},
            json={"matchUrl": "https://match.ichunqiu.com/demo-match"},
        )

    assert response.status_code == 200
    assert source.bind_tokens == [""]
    assert store.get().platform_token == "anonymous:not-a-real-token"


def test_platform_bind_does_not_replace_an_explicit_invalid_token(
    tmp_path: Path,
) -> None:
    class InvalidTokenStub(PlatformStub):
        def bind_match(self, _match_url: str, supplied_token: str = "") -> BindResult:
            self.bind_tokens.append(supplied_token)
            raise RuntimeError("invalid explicit session")

    source = InvalidTokenStub()
    store = RuntimeConfigStore(Config(), tmp_path / ".runtime-config.json")
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/bind",
            headers={"X-GCSIS-Action": "platform-auth"},
            json={
                "matchUrl": "https://match.ichunqiu.com/demo-match",
                "platformToken": "invalid:not-a-real-token",
            },
        )

    assert response.status_code == 502
    assert source.bind_tokens == ["invalid:not-a-real-token"]
    assert store.get().match_url == ""
    assert "invalid:not-a-real-token" not in response.text


def test_password_captcha_requirement_is_exposed_without_credentials(
    tmp_path: Path,
) -> None:
    class CaptchaRequiredStub(PlatformStub):
        def login_password(
            self, _account: str, _password: str, _image_code: str = ""
        ) -> LoginResult:
            raise APIError("1002", "请输入图形验证码")

    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="resolved-match-key",
            platform_token="anonymous:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, CaptchaRequiredStub()), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/login/password",
            headers={"X-GCSIS-Action": "platform-auth"},
            json={"account": "demo", "password": "not-a-real-password"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "1002"
    assert "not-a-real-password" not in response.text


def test_platform_hides_source_context_errors_from_login_responses(
    tmp_path: Path,
) -> None:
    class SourceContextErrorStub(PlatformStub):
        def login_sms(self, _phone: str, _sms_code: str) -> LoginResult:
            raise APIError("8", "未定义数组索引: s_source_id")

    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="resolved-match-key",
            platform_token="anonymous:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, SourceContextErrorStub()), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/login/sms",
            headers={"X-GCSIS-Action": "platform-auth"},
            json={"phone": "13800138000", "smsCode": "123456"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MATCH_CONTEXT_MISSING"
    assert "s_source_id" not in response.text


def test_platform_hides_source_context_errors_for_anonymous_auth_status(
    tmp_path: Path,
) -> None:
    class AnonymousContextStub(PlatformStub):
        def auth_status(self) -> AuthStatus:
            return AuthStatus(False, message="未定义数组索引: s_source_id")

    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="resolved-match-key",
            platform_token="not_login:match:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, AnonymousContextStub()), config_store=store)
    with TestClient(app) as client:
        response = client.get("/api/platform/auth")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "authenticated": False,
        "teamName": "",
        "message": "尚未登录",
        "teamId": "",
        "userName": "",
        "organization": "",
        "score": None,
        "rank": None,
        "memberCount": None,
    }
    assert "s_source_id" not in response.text


def test_platform_keeps_login_when_only_team_context_is_unavailable(
    tmp_path: Path,
) -> None:
    class LoginContextErrorStub(PlatformStub):
        def auth_status(self) -> AuthStatus:
            return AuthStatus(False, message="未定义数组索引: s_source_id")

    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="resolved-match-key",
            platform_token="login:match:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, LoginContextErrorStub()), config_store=store)
    with TestClient(app) as client:
        response = client.get("/api/platform/auth")

    assert response.status_code == 200
    assert response.json()["data"]["authenticated"] is True
    assert response.json()["data"]["message"] == "已登录，队伍信息暂不可用"
    assert "s_source_id" not in response.text


def test_platform_keeps_individual_login_without_team_information(
    tmp_path: Path,
) -> None:
    class IndividualLoginStub(PlatformStub):
        def auth_status(self) -> AuthStatus:
            return AuthStatus(False, message="暂无队伍信息", error_code="116")

    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="resolved-match-key",
            platform_token="login:individual:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, IndividualLoginStub()), config_store=store)
    with TestClient(app) as client:
        response = client.get("/api/platform/auth")

    assert response.status_code == 200
    assert response.json()["data"]["authenticated"] is True
    assert response.json()["data"]["message"] == "已登录，队伍信息暂不可用"


def test_platform_rejects_expired_login_token(tmp_path: Path) -> None:
    class ExpiredLoginStub(PlatformStub):
        def auth_status(self) -> AuthStatus:
            return AuthStatus(False, message="token已失效", error_code="114")

    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="resolved-match-key",
            platform_token="login:expired:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, ExpiredLoginStub()), config_store=store)
    with TestClient(app) as client:
        response = client.get("/api/platform/auth")

    assert response.status_code == 200
    assert response.json()["data"]["authenticated"] is False
    assert response.json()["data"]["message"] == "token已失效"


def test_platform_login_keeps_token_when_team_context_is_unavailable(
    tmp_path: Path,
) -> None:
    class LoginValidationErrorStub(PlatformStub):
        def auth_status(self) -> AuthStatus:
            return AuthStatus(False, message="未定义数组索引: s_source_id")

    source = LoginValidationErrorStub()
    store = RuntimeConfigStore(
        Config(
            match_url="https://match.ichunqiu.com/demo-match",
            match_key="resolved-match-key",
            platform_token="anonymous:not-a-real-token",
        ),
        tmp_path / ".runtime-config.json",
    )
    app = create_app(cast(AgentProtocol, source), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/platform/login/sms",
            headers={"X-GCSIS-Action": "platform-auth"},
            json={"phone": "13800138000", "smsCode": "123456"},
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "authenticated": True,
        "teamName": "",
        "message": "已登录，队伍信息暂不可用",
        "teamId": "",
        "userName": "",
        "organization": "",
        "score": None,
        "rank": None,
        "memberCount": None,
    }
    assert store.get().platform_token == "login:sms:not-a-real-token"
