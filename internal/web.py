"""FastAPI HTTP surface matching the original Go console."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypeGuard, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent import APIError, PlatformNotConfigured
from .attachment_tasks import DownloadTaskBusy, DownloadTaskNotFound
from .codex import CodexError, CodexUnavailable, ErrPrompt, TaskNotFound
from .download import (
    AttachmentNotFound,
    DownloadFailed,
    ExerciseNotFound,
    InvalidURL,
    StorageError,
    TooLarge,
)
from .environment import collect_environment_status
from .openai_client import ModelListError, fetch_models
from .solver import ModelNotConfigured
from .tool_manager import ToolBusyError
from .config import Config, ConfigError, RuntimeConfigStore
from .ichunqiu_api import AuthStatus, BindResult, CaptchaImage, LoginResult

MAX_REQUEST_BYTES = 2048
MAX_PLATFORM_REQUEST_BYTES = 16 * 1024
MAX_GO_INT64 = (1 << 63) - 1
ATTACHMENT_ACTION_HEADER = "X-GCSIS-Action"
ATTACHMENT_ACTION_PURPOSE = "attachment-manager"
PLATFORM_ACTION_PURPOSE = "platform-auth"
TOOLS_ACTION_PURPOSE = "tools-manager"
ModelT = TypeVar("ModelT", bound=BaseModel)
LOGGER = logging.getLogger("gcsis-tools.web")


class AgentProtocol(Protocol):
    def bind_match(self, match_url: str, supplied_token: str = "") -> BindResult: ...

    def auth_status(self) -> AuthStatus: ...

    def login_password(
        self, account: str, password: str, image_code: str = ""
    ) -> LoginResult: ...

    def send_sms_code(self, area_id: str, phone: str, rotation: int) -> None: ...

    def login_sms(self, phone: str, sms_code: str) -> LoginResult: ...

    def logout(self) -> None: ...

    def captcha(self, kind: Literal["image", "rotate"]) -> CaptchaImage: ...

    def invalidate(self) -> None: ...

    def overview(self, refresh: bool = False) -> Any: ...

    def match_info(self, refresh: bool = False) -> Any: ...

    def exercises(self, refresh: bool = False) -> Any: ...

    def exercise(self, exercise_id: int, refresh: bool = False) -> Any: ...

    def submit_flag(self, exercise_id: int, flag: str) -> Any: ...

    def build_environment(self, exercise_id: int) -> Any: ...

    def recover_environment(self, exercise_id: int) -> Any: ...

    def notices(self, refresh: bool = False) -> Any: ...

    def notice(self, notice_id: int, refresh: bool = False) -> Any: ...

    def schema_diagnostics(self) -> dict[str, Any]: ...


class DownloaderProtocol(Protocol):
    def download(self, exercise_id: int, attachment_index: int) -> Any: ...


class AttachmentManagerProtocol(DownloaderProtocol, Protocol):
    def catalog(self) -> Any: ...

    def download_all(self) -> Any: ...

    def download_exercise(self, exercise_id: int) -> Any: ...

    def clear_exercise(self, exercise_id: int) -> Any: ...

    def redownload_exercise(self, exercise_id: int) -> Any: ...

    def attachments(self, exercise_id: int) -> Any: ...


class AttachmentTasksProtocol(Protocol):
    def start_all(self) -> dict[str, Any]: ...

    def start_exercise(self, exercise_id: int) -> dict[str, Any]: ...

    def start_category(self, category: str) -> dict[str, Any]: ...

    def start_single(self, exercise_id: int, index: int) -> dict[str, Any]: ...

    def start_redownload(self, exercise_id: int) -> dict[str, Any]: ...

    def get(self, task_id: str) -> dict[str, Any]: ...

    def active(self) -> dict[str, Any] | None: ...

    def pause(self, task_id: str) -> dict[str, Any]: ...

    def resume(self, task_id: str) -> dict[str, Any]: ...

    def cancel(self, task_id: str) -> dict[str, Any]: ...

    def start_probe(self, force: bool = False) -> dict[str, Any]: ...

    def probe_status(self) -> dict[str, Any]: ...


class AIProtocol(Protocol):
    def enabled(self) -> bool: ...

    def prompt(self, exercise_id: int) -> Any: ...

    def run(self, exercise_id: int) -> Any: ...


class ToolManagerProtocol(Protocol):
    def catalog(self) -> dict[str, Any]: ...

    def install(self, names: list[str] | None = None, all_tools: bool = False) -> dict[str, Any]: ...

    def cancel(self) -> dict[str, Any]: ...

    def uninstall(self, name: str) -> dict[str, Any]: ...


class CodexProtocol(Protocol):
    def enabled(self) -> bool: ...

    def list(self) -> Any: ...

    def get(self, task_id: str) -> Any: ...

    def cancel(self, task_id: str) -> Any: ...

    def continue_task(self, task_id: str) -> Any: ...

    def delete(self, task_id: str) -> Any: ...

    def start(self, exercise_id: int, prompt_override: str = "") -> Any: ...

    def start_pure(self, exercise_id: int, prompt_override: str = "") -> Any: ...

    def follow_up(self, task_id: str, message: str) -> Any: ...

    def side(self, task_id: str, message: str) -> Any: ...

    def open_terminal(self, task_id: str) -> dict[str, Any]: ...

    def open_challenge_folder(self, task_id: str) -> dict[str, Any]: ...

    def events_page(self, task_id: str, before: int = 0, limit: int = 64) -> dict[str, Any]: ...

    def log_text(self, task_id: str) -> str: ...

    def log_json(self, task_id: str) -> Any: ...

    def open_log_file(self, task_id: str) -> dict[str, Any]: ...

    def rename(self, task_id: str, title: str) -> Any: ...

    def update_pending_message(self, task_id: str, message: str) -> Any: ...

    def cancel_pending_message(self, task_id: str) -> Any: ...

    def resume_interrupted(self, task_id: str) -> Any: ...

    def mark_finished(self, task_id: str) -> Any: ...


class FlagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    flag: str = Field(min_length=1, max_length=256)


class RuntimeConfigPatch(BaseModel):
    """Web-editable settings; omitted secrets keep their current value."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )
    model_base_url: str | None = Field(default=None, alias="modelBaseUrl")
    model_api_key: str | None = Field(default=None, alias="modelApiKey")
    model_name: str | None = Field(default=None, alias="modelName")
    codex_base_url: str | None = Field(default=None, alias="codexBaseUrl")
    codex_api_key: str | None = Field(default=None, alias="codexApiKey")
    codex_model: str | None = Field(default=None, alias="codexModel")
    codex_max_concurrency: int | None = Field(
        default=None, alias="codexMaxConcurrency", ge=1, le=16
    )
    codex_system_prompt: str | None = Field(
        default=None, alias="codexSystemPrompt", max_length=20_000
    )
    codex_ctf_skills_enabled: bool | None = Field(
        default=None, alias="codexCtfSkillsEnabled"
    )
    codex_auto_resume_interrupted: bool | None = Field(
        default=None, alias="codexAutoResumeInterrupted"
    )


class ModelListRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, alias="apiKey", max_length=4096)
    kind: Literal["model", "codex"] = "model"


class CodexStartRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    system_prompt: str = Field(
        default="", alias="systemPrompt", max_length=20_000
    )


class CodexMessageRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    message: str = Field(min_length=1, max_length=16_000)
    side: bool = False


class CodexRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)


class CodexPendingMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=16_000)


class MatchBindRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    match_url: str = Field(alias="matchUrl", min_length=1, max_length=2048)
    platform_token: str = Field(default="", alias="platformToken", max_length=4096)


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    account: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    image_code: str = Field(
        default="",
        alias="imageCode",
        pattern=r"^(?:[A-Za-z0-9_]{4})?$",
    )


class SMSSendRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    area_id: str = Field(
        alias="areaId", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    phone: str = Field(min_length=5, max_length=20, pattern=r"^[0-9]+$")
    rotation: int = Field(ge=0, le=360)


class SMSLoginRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    phone: str = Field(min_length=5, max_length=20, pattern=r"^[0-9]+$")
    sms_code: str = Field(
        alias="smsCode", min_length=4, max_length=8, pattern=r"^[0-9]+$"
    )


class ToolInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    tool_ids: list[str] = Field(default_factory=list, alias="toolIds", max_length=64)
    all_tools: bool = Field(default=False, alias="all")

    @model_validator(mode="after")
    def validate_selection(self) -> "ToolInstallRequest":
        if not self.all_tools and not self.tool_ids:
            raise ValueError("至少选择一个工具")
        return self


class AttachmentDownloadRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )
    scope: Literal["all", "category", "exercise", "single", "redownload"]
    category: str | None = Field(default=None, max_length=128)
    exercise_id: int | None = Field(default=None, alias="exerciseId", ge=1)
    attachment_index: int | None = Field(default=None, alias="attachmentIndex", ge=0)

    @model_validator(mode="after")
    def validate_scope_fields(self) -> "AttachmentDownloadRequest":
        if self.scope == "all":
            if self.exercise_id is not None or self.attachment_index is not None or self.category is not None:
                raise ValueError("all scope does not accept attachment selection")
            return self
        if self.scope == "category":
            if self.category is None or not self.category.strip():
                raise ValueError("category is required")
            if self.exercise_id is not None or self.attachment_index is not None:
                raise ValueError("category scope does not accept exercise selection")
            return self
        if self.exercise_id is None:
            raise ValueError("exerciseId is required")
        if self.scope == "single" and self.attachment_index is None:
            raise ValueError("attachmentIndex is required")
        if self.scope != "single" and self.attachment_index is not None:
            raise ValueError("attachmentIndex is only valid for single scope")
        if self.category is not None:
            raise ValueError("category is only valid for category scope")
        return self


class Services:
    def __init__(
        self,
        agent: AgentProtocol,
        downloader: DownloaderProtocol | AttachmentManagerProtocol | None = None,
        ai: AIProtocol | None = None,
        codex: CodexProtocol | None = None,
        attachments: AttachmentManagerProtocol | None = None,
        attachment_tasks: AttachmentTasksProtocol | None = None,
        tools: ToolManagerProtocol | None = None,
    ) -> None:
        self.agent = agent
        self.downloader = downloader
        self.ai = ai
        self.codex = codex
        self.attachments = (
            attachments
            if attachments is not None
            else (downloader if _has_attachment_manager(downloader) else None)
        )
        self.attachment_tasks = attachment_tasks
        self.tools = tools


def create_app(
    agent: AgentProtocol,
    downloader: DownloaderProtocol | AttachmentManagerProtocol | None = None,
    ai: AIProtocol | None = None,
    codex: CodexProtocol | None = None,
    attachments: AttachmentManagerProtocol | None = None,
    static_dir: str | Path | None = None,
    config_store: RuntimeConfigStore | None = None,
    attachment_tasks: AttachmentTasksProtocol | None = None,
    tools: ToolManagerProtocol | None = None,
    environment_workspace: str | Path | None = None,
) -> FastAPI:
    services = Services(agent, downloader, ai, codex, attachments, attachment_tasks, tools)
    app = FastAPI(
        title="CTF 比赛工作台", docs_url=None, redoc_url=None, redirect_slashes=False
    )
    app.state.services = services
    app.state.config_store = config_store
    app.state.environment_workspace = Path(environment_workspace or Path.cwd()).resolve()

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[..., Any]) -> Any:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self'; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 405:
            headers = (
                {"Allow": str(exc.headers.get("Allow", ""))} if exc.headers else {}
            )
            return _error_response(
                405, "METHOD_NOT_ALLOWED", "请求方法不受支持", headers
            )
        if exc.status_code == 404:
            return _error_response(404, "NOT_FOUND", "请求的接口不存在")
        return _error_response(exc.status_code, "HTTP_ERROR", "请求失败")

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(400, "INVALID_REQUEST", "请求格式无效")

    @app.api_route(
        "/api/config",
        methods=["GET", "PUT", "POST", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def runtime_config(request: Request) -> JSONResponse:
        if config_store is None:
            return _error_response(503, "CONFIG_UNAVAILABLE", "运行时配置服务未启用")
        if request.method == "GET":
            return _success_response(config_store.public())
        if request.method != "PUT":
            return _method_error("GET, PUT")
        try:
            body = await request.body()
            if len(body) > 32 * 1024:
                raise ValueError
            value = json.loads(body.decode("utf-8"))
            patch = RuntimeConfigPatch.model_validate(value)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            return _error_response(400, "INVALID_CONFIG", "配置格式无效")
        try:
            updated = await asyncio.to_thread(
                config_store.update,
                patch.model_dump(exclude_unset=True),
            )
        except ConfigError as exc:
            return _error_response(400, "INVALID_CONFIG", str(exc))
        app.state.config = updated
        return _success_response(config_store.public())

    @app.api_route(
        "/api/models",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def model_catalog(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        parsed, error = await _validated_request(request, ModelListRequest)
        if error is not None:
            return error
        assert parsed is not None
        api_key = parsed.api_key or ""
        if not api_key and config_store is not None:
            current = config_store.get()
            api_key = (
                current.codex_api_key
                if parsed.kind == "codex"
                else current.model_api_key
            )
        try:
            models = await asyncio.to_thread(fetch_models, parsed.base_url, api_key)
        except ModelListError as exc:
            return _error_response(502, "MODEL_LIST_FAILED", str(exc))
        return _success_response({"models": models, "kind": parsed.kind})

    @app.api_route(
        "/api/platform/bind",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def bind_platform(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_platform_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "平台操作缺少同源凭据"
            )
        if config_store is None:
            return _error_response(503, "CONFIG_UNAVAILABLE", "运行时配置服务未启用")
        parsed, error = await _validated_request(request, MatchBindRequest)
        if error is not None:
            return error
        assert parsed is not None
        try:
            current = config_store.get()
            same_match = current.match_url.rstrip("/") == parsed.match_url.rstrip("/")
            saved_token = current.platform_token if same_match else ""
            result = await asyncio.to_thread(
                _bind_match_with_recovery,
                services.agent,
                parsed.match_url,
                parsed.platform_token,
                saved_token,
            )
            updated = await asyncio.to_thread(
                config_store.update,
                {
                    "match_url": result.match_url,
                    "match_key": result.match_key,
                    "platform_token": result.platform_token,
                    "match_title": str(result.match.get("title", "") or ""),
                    "match_login_type": int(result.match.get("login_type", 0) or 0),
                    "match_source_id": _match_source_id(result.match),
                },
            )
            services.agent.invalidate()
        except Exception as exc:
            return _platform_error(exc)
        app.state.config = updated
        public = config_store.public()
        public["authenticated"] = result.authenticated
        return _success_response(public)

    @app.api_route(
        "/api/platform/auth",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def platform_auth(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        if config_store is None:
            return _error_response(503, "CONFIG_UNAVAILABLE", "运行时配置服务未启用")
        try:
            status = await asyncio.to_thread(services.agent.auth_status)
            status = _runtime_login_status(status, config_store)
        except Exception as exc:
            return _platform_error(exc)
        return _success_response(status)

    @app.api_route(
        "/api/platform/schema",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def platform_schema(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        reader = getattr(services.agent, "schema_diagnostics", None)
        if not callable(reader):
            return _success_response({"changes": [], "interfaces": {}})
        try:
            result = await asyncio.to_thread(reader)
        except Exception as exc:
            LOGGER.warning("平台接口字段诊断读取失败", exc_info=exc)
            return _error_response(500, "SCHEMA_DIAGNOSTICS_FAILED", "接口字段诊断暂时不可用")
        return _success_response(result)

    @app.api_route(
        "/api/platform/logout",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def platform_logout(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_platform_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "平台操作缺少同源凭据"
            )
        if config_store is None:
            return _error_response(503, "CONFIG_UNAVAILABLE", "运行时配置服务未启用")
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        try:
            updated = await asyncio.to_thread(
                _logout_and_refresh_context, services.agent, config_store
            )
        except Exception as exc:
            return _platform_error(exc)
        app.state.config = updated
        public = config_store.public()
        public["authenticated"] = False
        return _success_response(public)

    @app.api_route(
        "/api/platform/captcha",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def platform_captcha(request: Request) -> Response:
        if not _method(request, "GET"):
            return _method_error("GET")
        raw_kind = request.query_params.get("kind", "").strip()
        if raw_kind not in {"image", "rotate"}:
            return _error_response(400, "INVALID_CAPTCHA_KIND", "验证码类型无效")
        kind: Literal["image", "rotate"] = "image" if raw_kind == "image" else "rotate"
        try:
            if config_store is not None and config_store.get().match_url:
                # ``/match/sso/detail`` issues a short-lived dynamic ``k`` for
                # slug URLs.  Always refresh the anonymous context before a
                # new captcha so the subsequent SMS/password request uses the
                # same context as the image challenge.
                await asyncio.to_thread(
                    _refresh_anonymous_match_context,
                    services.agent,
                    config_store,
                )
                app.state.config = config_store.get()
            image = await asyncio.to_thread(services.agent.captcha, kind)
        except Exception as exc:
            return _platform_error(exc)
        return Response(
            content=image.body,
            media_type=image.content_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.api_route(
        "/api/platform/login/password",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def platform_password_login(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_platform_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "平台操作缺少同源凭据"
            )
        if config_store is None:
            return _error_response(503, "CONFIG_UNAVAILABLE", "运行时配置服务未启用")
        parsed, error = await _validated_request(request, PasswordLoginRequest)
        if error is not None:
            return error
        assert parsed is not None
        try:
            login = await asyncio.to_thread(
                services.agent.login_password,
                parsed.account,
                parsed.password,
                parsed.image_code,
            )
            updated = await asyncio.to_thread(
                config_store.update,
                {
                    "platform_token": login.platform_token,
                    "match_key": login.match_key,
                },
            )
            services.agent.invalidate()
            status = await asyncio.to_thread(services.agent.auth_status)
            if not status.authenticated and _is_source_context_error(status.message):
                status = AuthStatus(True, message="已登录，队伍信息暂不可用")
            status = _login_result_status(status)
        except Exception as exc:
            return _platform_error(exc)
        app.state.config = updated
        return _success_response(status)

    @app.api_route(
        "/api/platform/login/sms/send",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def platform_sms_send(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_platform_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "平台操作缺少同源凭据"
            )
        parsed, error = await _validated_request(request, SMSSendRequest)
        if error is not None:
            return error
        assert parsed is not None
        try:
            await asyncio.to_thread(
                services.agent.send_sms_code,
                parsed.area_id,
                parsed.phone,
                parsed.rotation,
            )
        except Exception as exc:
            return _platform_error(exc)
        return _success_response({"accepted": True})

    @app.api_route(
        "/api/platform/login/sms",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def platform_sms_login(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_platform_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "平台操作缺少同源凭据"
            )
        if config_store is None:
            return _error_response(503, "CONFIG_UNAVAILABLE", "运行时配置服务未启用")
        parsed, error = await _validated_request(request, SMSLoginRequest)
        if error is not None:
            return error
        assert parsed is not None
        try:
            login = await asyncio.to_thread(
                services.agent.login_sms,
                parsed.phone,
                parsed.sms_code,
            )
            updated = await asyncio.to_thread(
                config_store.update,
                {
                    "platform_token": login.platform_token,
                    "match_key": login.match_key,
                },
            )
            services.agent.invalidate()
            status = await asyncio.to_thread(services.agent.auth_status)
            if not status.authenticated and _is_source_context_error(status.message):
                status = AuthStatus(True, message="已登录，队伍信息暂不可用")
            status = _login_result_status(status)
        except Exception as exc:
            return _platform_error(exc)
        app.state.config = updated
        return _success_response(status)

    @app.api_route(
        "/api/overview",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def overview(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        return await _service_response(services.agent.overview, _refresh(request))

    @app.api_route(
        "/api/match-info",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def match_info(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        return await _service_response(services.agent.match_info, _refresh(request))

    @app.api_route(
        "/api/exercises",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def exercises(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        return await _service_response(services.agent.exercises, _refresh(request))

    @app.api_route(
        "/api/exercises/{raw_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def exercise_routes(request: Request, raw_path: str) -> JSONResponse:
        return await _exercise_route(services, request, raw_path)

    @app.api_route(
        "/api/notices",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def notices(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        return await _service_response(services.agent.notices, _refresh(request))

    @app.api_route(
        "/api/notices/{raw_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def notice(request: Request, raw_path: str) -> JSONResponse:
        raw_id = raw_path.strip("/")
        if not raw_id or "/" in raw_id:
            return _error_response(404, "NOT_FOUND", "请求的接口不存在")
        if not _method(request, "GET"):
            return _method_error("GET")
        notice_id = _parse_id(raw_id)
        if notice_id is None:
            return _error_response(400, "INVALID_ID", "ID 必须是正整数")
        return await _service_response(
            services.agent.notice, notice_id, _refresh(request)
        )

    @app.api_route(
        "/api/attachments",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def attachment_catalog(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        if services.attachments is None:
            return _error_response(
                503, "ATTACHMENT_MANAGER_UNAVAILABLE", "附件管理服务未启用"
            )
        return await _attachment_response(services.attachments.catalog)

    @app.api_route(
        "/api/attachments/download-all",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def download_all(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_attachment_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "附件管理操作缺少同源凭据"
            )
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if services.attachments is None:
            return _error_response(
                503, "ATTACHMENT_MANAGER_UNAVAILABLE", "附件管理服务未启用"
            )
        return await _attachment_response(services.attachments.download_all)

    @app.api_route(
        "/api/attachments/downloads",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def attachment_downloads(request: Request) -> JSONResponse:
        if services.attachment_tasks is None:
            return _error_response(
                503, "ATTACHMENT_TASKS_UNAVAILABLE", "附件下载任务服务未启用"
            )
        if request.method == "GET":
            return _success_response(services.attachment_tasks.active())
        if request.method != "POST":
            return _method_error("GET, POST")
        if not _trusted_attachment_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "附件管理操作缺少同源凭据"
            )
        parsed, error = await _validated_request(request, AttachmentDownloadRequest)
        if error is not None:
            return error
        assert parsed is not None
        try:
            task = await asyncio.to_thread(
                _start_attachment_download, services.attachment_tasks, parsed
            )
        except DownloadTaskBusy:
            return _error_response(
                409, "ATTACHMENT_TASK_BUSY", "已有附件下载任务正在运行"
            )
        return _success_response(task, 202)

    @app.api_route(
        "/api/attachments/downloads/{task_id}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def attachment_download_task(request: Request, task_id: str) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        if not _valid_task_id(task_id):
            return _error_response(400, "INVALID_TASK_ID", "附件任务 ID 无效")
        if services.attachment_tasks is None:
            return _error_response(
                503, "ATTACHMENT_TASKS_UNAVAILABLE", "附件下载任务服务未启用"
            )
        try:
            return _success_response(services.attachment_tasks.get(task_id))
        except DownloadTaskNotFound:
            return _error_response(
                404, "ATTACHMENT_TASK_NOT_FOUND", "附件下载任务不存在"
            )

    @app.api_route(
        "/api/attachments/downloads/{task_id}/{action}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def control_attachment_download(
        request: Request, task_id: str, action: str
    ) -> JSONResponse:
        if action not in {"pause", "resume", "cancel"}:
            return _error_response(404, "NOT_FOUND", "请求的接口不存在")
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _valid_task_id(task_id):
            return _error_response(400, "INVALID_TASK_ID", "附件任务 ID 无效")
        if not _trusted_attachment_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "附件管理操作缺少同源凭据"
            )
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if services.attachment_tasks is None:
            return _error_response(
                503, "ATTACHMENT_TASKS_UNAVAILABLE", "附件下载任务服务未启用"
            )
        operation = getattr(services.attachment_tasks, action)
        try:
            return _success_response(await asyncio.to_thread(operation, task_id))
        except DownloadTaskNotFound:
            return _error_response(
                404, "ATTACHMENT_TASK_NOT_FOUND", "附件下载任务不存在"
            )

    @app.api_route(
        "/api/attachments/sizes",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def attachment_sizes(request: Request) -> JSONResponse:
        if services.attachment_tasks is None:
            return _error_response(
                503, "ATTACHMENT_TASKS_UNAVAILABLE", "附件大小探测服务未启用"
            )
        if request.method == "GET":
            return _success_response(services.attachment_tasks.probe_status())
        if request.method != "POST":
            return _method_error("GET, POST")
        if not _trusted_attachment_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "附件管理操作缺少同源凭据"
            )
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        return _success_response(
            services.attachment_tasks.start_probe(force=_refresh(request)), 202
        )

    @app.api_route(
        "/api/tools",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def tools_catalog(request: Request) -> JSONResponse:
        if services.tools is None:
            return _error_response(503, "TOOLS_UNAVAILABLE", "工具管理服务未启用")
        if request.method == "GET":
            return _success_response(await asyncio.to_thread(services.tools.catalog))
        if request.method != "POST":
            return _method_error("GET, POST")
        if request.headers.get(ATTACHMENT_ACTION_HEADER) != TOOLS_ACTION_PURPOSE:
            return _error_response(403, "CROSS_SITE_ACTION_REJECTED", "工具操作缺少同源凭据")
        parsed, error = await _validated_request(request, ToolInstallRequest)
        if error is not None:
            return error
        assert parsed is not None
        try:
            return _success_response(
                await asyncio.to_thread(services.tools.install, parsed.tool_ids, parsed.all_tools),
                202,
            )
        except ValueError as exc:
            return _error_response(400, "INVALID_TOOL_SELECTION", str(exc))

    async def _uninstall_tool(request: Request, tool_id: str) -> JSONResponse:
        if services.tools is None:
            return _error_response(503, "TOOLS_UNAVAILABLE", "工具管理服务未启用")
        if not _trusted_tools_request(request):
            return _error_response(403, "CROSS_SITE_ACTION_REJECTED", "工具操作缺少同源凭据")
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if not tool_id or len(tool_id) > 64 or "/" in tool_id:
            return _error_response(400, "INVALID_TOOL_ID", "工具标识无效")
        try:
            return _success_response(
                await asyncio.to_thread(services.tools.uninstall, tool_id)
            )
        except ValueError:
            return _error_response(404, "TOOL_NOT_FOUND", "工具不存在")
        except ToolBusyError as exc:
            return _error_response(409, "TOOL_TASK_BUSY", str(exc))
        except Exception:
            LOGGER.exception("tool uninstall failed for %s", tool_id)
            return _error_response(500, "TOOL_UNINSTALL_FAILED", "工具卸载失败，请查看服务日志")

    @app.api_route(
        "/api/tools/cancel",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def cancel_tools(request: Request) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        if request.headers.get(ATTACHMENT_ACTION_HEADER) != TOOLS_ACTION_PURPOSE:
            return _error_response(403, "CROSS_SITE_ACTION_REJECTED", "工具操作缺少同源凭据")
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if services.tools is None:
            return _error_response(503, "TOOLS_UNAVAILABLE", "工具管理服务未启用")
        return _success_response(await asyncio.to_thread(services.tools.cancel))

    @app.api_route(
        "/api/tools/{tool_id}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def uninstall_tool(request: Request, tool_id: str) -> JSONResponse:
        if not _method(request, "DELETE"):
            return _method_error("DELETE")
        return await _uninstall_tool(request, tool_id)

    @app.api_route(
        "/api/tools/{tool_id}/uninstall",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def uninstall_tool_action(request: Request, tool_id: str) -> JSONResponse:
        if not _method(request, "POST"):
            return _method_error("POST")
        return await _uninstall_tool(request, tool_id)

    @app.api_route(
        "/api/environment",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def environment_status(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        workspace = getattr(app.state, "environment_workspace", Path.cwd())
        return _success_response(await asyncio.to_thread(collect_environment_status, workspace))

    @app.api_route(
        "/api/codex/tasks",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def codex_tasks(request: Request) -> JSONResponse:
        if not _method(request, "GET"):
            return _method_error("GET")
        if services.codex is None or not services.codex.enabled():
            if services.codex is None:
                return _error_response(503, "CODEX_UNAVAILABLE", "Codex CLI 未配置或不可用")
        assert services.codex is not None
        data = _wire(services.codex.list())
        if isinstance(data, dict):
            data["available"] = services.codex.enabled()
        return _success_response(data)

    @app.api_route(
        "/api/codex/tasks/{task_id}",
        methods=["DELETE"],
    )
    async def delete_codex_task(request: Request, task_id: str) -> JSONResponse:
        if services.codex is None:
            return _error_response(503, "CODEX_UNAVAILABLE", "Codex CLI 未配置或不可用")
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        try:
            await asyncio.to_thread(services.codex.delete, task_id)
            return _success_response({"deleted": True})
        except Exception as exc:
            return _codex_error(exc)

    @app.api_route(
        "/api/codex/tasks/{raw_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def codex_task(request: Request, raw_path: str) -> Response:
        parts = [part for part in raw_path.strip("/").split("/") if part]
        if not parts or len(parts) > 2:
            return _error_response(404, "NOT_FOUND", "请求的接口不存在")
        if services.codex is None:
            return _error_response(503, "CODEX_UNAVAILABLE", "Codex CLI 未配置或不可用")
        if len(parts) == 1:
            if request.method == "DELETE":
                body_error = await _empty_body_error(request)
                if body_error is not None:
                    return body_error
                try:
                    await asyncio.to_thread(services.codex.delete, parts[0])
                    return _success_response({"deleted": True})
                except Exception as exc:
                    return _codex_error(exc)
            if not _method(request, "GET"):
                return _method_error("GET")
            try:
                return _success_response(
                    await asyncio.to_thread(services.codex.get, parts[0])
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "events":
            if not _method(request, "GET"):
                return _method_error("GET")
            try:
                before = int(request.query_params.get("before", "0") or 0)
                limit = int(request.query_params.get("limit", "64") or 64)
            except ValueError:
                return _error_response(400, "INVALID_REQUEST", "日志分页参数无效")
            try:
                return _success_response(
                    await asyncio.to_thread(
                        services.codex.events_page, parts[0], before, limit
                    )
                )
            except ValueError as exc:
                return _error_response(400, "INVALID_REQUEST", str(exc))
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "logs.txt":
            if not _method(request, "GET"):
                return _method_error("GET")
            try:
                content = await asyncio.to_thread(services.codex.log_text, parts[0])
                return Response(
                    content=content,
                    media_type="text/plain; charset=utf-8",
                    headers={
                        "Cache-Control": "no-store",
                        "Content-Disposition": f'attachment; filename="codex-{parts[0]}.txt"',
                    },
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "logs.json":
            if not _method(request, "GET"):
                return _method_error("GET")
            try:
                content = await asyncio.to_thread(services.codex.log_json, parts[0])
                return Response(
                    content=json.dumps(content, ensure_ascii=False, indent=2),
                    media_type="application/json; charset=utf-8",
                    headers={
                        "Cache-Control": "no-store",
                        "Content-Disposition": f'attachment; filename="codex-{parts[0]}.json"',
                    },
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "log-file":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                return _success_response(
                    await asyncio.to_thread(services.codex.open_log_file, parts[0])
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "rename":
            if not _method(request, "PATCH"):
                return _method_error("PATCH")
            rename_request, error = await _validated_request(
                request, CodexRenameRequest
            )
            if error is not None:
                return error
            assert rename_request is not None
            try:
                return _success_response(
                    await asyncio.to_thread(
                        services.codex.rename, parts[0], rename_request.title
                    )
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "pending-message":
            if request.method == "PATCH":
                pending_request, error = await _validated_request(
                    request, CodexPendingMessageRequest
                )
                if error is not None:
                    return error
                assert pending_request is not None
                try:
                    return _success_response(
                        await asyncio.to_thread(
                            services.codex.update_pending_message,
                            parts[0],
                            pending_request.message,
                        )
                    )
                except Exception as exc:
                    return _codex_error(exc)
            if request.method == "DELETE":
                body_error = await _empty_body_error(request)
                if body_error is not None:
                    return body_error
                try:
                    return _success_response(
                        await asyncio.to_thread(
                            services.codex.cancel_pending_message, parts[0]
                        )
                    )
                except Exception as exc:
                    return _codex_error(exc)
            return _method_error("PATCH, DELETE")
        if len(parts) == 2 and parts[1] == "resume":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                return _success_response(
                    await asyncio.to_thread(services.codex.resume_interrupted, parts[0])
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "finish":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                return _success_response(
                    await asyncio.to_thread(services.codex.mark_finished, parts[0])
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "message":
            if not _method(request, "POST"):
                return _method_error("POST")
            message_request, error = await _validated_request(
                request, CodexMessageRequest
            )
            if error is not None:
                return error
            assert message_request is not None
            try:
                operation = (
                    services.codex.side
                    if message_request.side
                    else services.codex.follow_up
                )
                return _success_response(
                    await asyncio.to_thread(
                        operation, parts[0], message_request.message
                    )
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "cancel":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                return _success_response(
                    await asyncio.to_thread(services.codex.cancel, parts[0])
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "continue":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                return _success_response(
                    await asyncio.to_thread(services.codex.continue_task, parts[0])
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "terminal":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                return _success_response(
                    await asyncio.to_thread(services.codex.open_terminal, parts[0])
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "folder":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                return _success_response(
                    await asyncio.to_thread(
                        services.codex.open_challenge_folder, parts[0]
                    )
                )
            except Exception as exc:
                return _codex_error(exc)
        if len(parts) == 2 and parts[1] == "delete":
            if not _method(request, "POST"):
                return _method_error("POST")
            body_error = await _empty_body_error(request)
            if body_error is not None:
                return body_error
            try:
                await asyncio.to_thread(services.codex.delete, parts[0])
                return _success_response({"deleted": True})
            except Exception as exc:
                return _codex_error(exc)
        return _error_response(404, "NOT_FOUND", "请求的接口不存在")

    static_path = (
        Path(static_dir)
        if static_dir
        else Path(__file__).resolve().parent.parent / "static"
    )
    if static_path.is_dir():
        app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
    return app


async def _exercise_route(
    services: Services, request: Request, raw_path: str
) -> JSONResponse:
    if request.url.path.endswith("/"):
        return _error_response(404, "NOT_FOUND", "请求的接口不存在")
    parts = raw_path.split("/")
    if not parts or not parts[0]:
        return _error_response(404, "NOT_FOUND", "请求的接口不存在")
    exercise_id = _parse_id(parts[0])
    if exercise_id is None:
        return _error_response(400, "INVALID_ID", "ID 必须是正整数")
    tail = parts[1:]
    if not tail:
        if not _method(request, "GET"):
            return _method_error("GET")
        return await _exercise_detail_response(services, exercise_id, _refresh(request))
    if tail == ["answer"]:
        if not _method(request, "POST"):
            return _method_error("POST")
        return await _answer(services, request, exercise_id)
    if tail == ["environment", "start"]:
        if not _method(request, "POST"):
            return _method_error("POST")
        return await _service_response(
            services.agent.build_environment, exercise_id, data={"accepted": True}
        )
    if tail == ["environment", "stop"]:
        if not _method(request, "POST"):
            return _method_error("POST")
        return await _service_response(
            services.agent.recover_environment, exercise_id, data={"accepted": True}
        )
    if len(tail) == 3 and tail[0] == "attachments" and tail[2] == "download":
        if not _method(request, "POST"):
            return _method_error("POST")
        try:
            index = int(tail[1])
        except ValueError:
            index = -1
        if index < 0 or index > MAX_GO_INT64:
            return _error_response(
                400, "INVALID_ATTACHMENT_INDEX", "附件索引必须是非负整数"
            )
        if services.downloader is None:
            return _error_response(
                503, "DOWNLOAD_UNAVAILABLE", "本地附件下载服务未启用"
            )
        return await _attachment_response(
            services.downloader.download, exercise_id, index
        )
    if tail == ["attachments", "download-all"]:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_attachment_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "附件管理操作缺少同源凭据"
            )
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if services.attachments is None:
            return _error_response(
                503, "ATTACHMENT_MANAGER_UNAVAILABLE", "附件管理服务未启用"
            )
        return await _attachment_response(
            services.attachments.download_exercise, exercise_id
        )
    if tail == ["attachments"]:
        if not _method(request, "DELETE"):
            return _method_error("DELETE")
        if not _trusted_attachment_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "附件管理操作缺少同源凭据"
            )
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if services.attachments is None:
            return _error_response(
                503, "ATTACHMENT_MANAGER_UNAVAILABLE", "附件管理服务未启用"
            )
        return await _attachment_response(
            services.attachments.clear_exercise, exercise_id
        )
    if tail == ["attachments", "redownload"]:
        if not _method(request, "POST"):
            return _method_error("POST")
        if not _trusted_attachment_request(request):
            return _error_response(
                403, "CROSS_SITE_ACTION_REJECTED", "附件管理操作缺少同源凭据"
            )
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if services.attachments is None:
            return _error_response(
                503, "ATTACHMENT_MANAGER_UNAVAILABLE", "附件管理服务未启用"
            )
        return await _attachment_response(
            services.attachments.redownload_exercise, exercise_id
        )
    if tail == ["ai", "prompt"]:
        if not _method(request, "GET"):
            return _method_error("GET")
        if services.ai is None:
            return _error_response(503, "AI_UNAVAILABLE", "AI 提示词服务未启用")
        return await _service_response(services.ai.prompt, exercise_id)
    if tail == ["ai", "run"]:
        if not _method(request, "POST"):
            return _method_error("POST")
        body_error = await _empty_body_error(request)
        if body_error is not None:
            return body_error
        if services.ai is None or not services.ai.enabled():
            return _error_response(503, "MODEL_NOT_CONFIGURED", "OpenAI 兼容模型未配置")
        return await _service_response(services.ai.run, exercise_id)
    if tail in (["codex", "run"], ["codex", "pure"]):
        if not _method(request, "POST"):
            return _method_error("POST")
        parsed, error = await _optional_validated_request(request, CodexStartRequest)
        if error is not None:
            return error
        assert parsed is not None
        if services.codex is None or not services.codex.enabled():
            return _error_response(503, "CODEX_UNAVAILABLE", "Codex CLI 未配置或不可用")
        try:
            snapshot = await asyncio.to_thread(
                services.codex.start_pure
                if tail[1] == "pure"
                else services.codex.start,
                exercise_id,
                parsed.system_prompt,
            )
            data = {
                "taskId": snapshot.id,
                "exerciseId": snapshot.exercise_id,
                "status": snapshot.status,
                "mode": snapshot.mode,
            }
            if snapshot.queue_position:
                data["queuePosition"] = snapshot.queue_position
            return _success_response(data, 202)
        except Exception as exc:
            return _codex_error(exc)
    return _error_response(404, "NOT_FOUND", "请求的接口不存在")


async def _answer(
    services: Services, request: Request, exercise_id: int
) -> JSONResponse:
    try:
        body = await request.body()
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError
        value = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        if exc.msg == "Extra data":
            return _error_response(
                400, "INVALID_JSON", "Flag 请求只能包含一个 JSON 对象"
            )
        return _error_response(400, "INVALID_JSON", "Flag 请求格式无效")
    except (UnicodeDecodeError, ValueError, TypeError):
        return _error_response(400, "INVALID_JSON", "Flag 请求格式无效")
    try:
        parsed = FlagRequest.model_validate(value)
    except ValidationError as exc:
        code, message = _flag_validation_error(exc)
        return _error_response(400, code, message)
    try:
        result = await asyncio.to_thread(
            services.agent.submit_flag, exercise_id, parsed.flag
        )
        return _success_response(result)
    except Exception as exc:
        upstream = _caused_by(exc, APIError)
        if upstream is not None:
            return _error_response(502, upstream.code, upstream.message)
        if _caused_by(exc, PlatformNotConfigured) is not None:
            return _error_response(503, "PLATFORM_NOT_CONFIGURED", "请先绑定 i春秋比赛")
        return _error_response(500, "INTERNAL_ERROR", "服务暂时不可用")


async def _validated_request(
    request: Request,
    model: type[ModelT],
) -> tuple[ModelT | None, JSONResponse | None]:
    try:
        body = await request.body()
        if len(body) > MAX_PLATFORM_REQUEST_BYTES:
            raise ValueError
        value = json.loads(body.decode("utf-8"))
        parsed = model.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return None, _error_response(400, "INVALID_REQUEST", "请求格式无效")
    return parsed, None


async def _optional_validated_request(
    request: Request,
    model: type[ModelT],
    maximum_bytes: int = 32 * 1024,
) -> tuple[ModelT | None, JSONResponse | None]:
    body = await request.body()
    if not body.strip():
        return model(), None
    if len(body) > maximum_bytes:
        return None, _error_response(400, "INVALID_REQUEST", "请求格式无效")
    try:
        value = json.loads(body.decode("utf-8"))
        parsed = model.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return None, _error_response(400, "INVALID_REQUEST", "请求格式无效")
    return parsed, None


def _platform_error(error: Exception) -> JSONResponse:
    upstream = _caused_by(error, APIError)
    if upstream is not None:
        if upstream.status == 429 or upstream.code == "429":
            return _error_response(
                429, upstream.code or "RATE_LIMITED", upstream.message
            )
        if upstream.code in {"112", "114", "130", "1002"}:
            return _error_response(401, upstream.code, upstream.message)
        if upstream.code == "8" and "s_source_id" in upstream.message:
            return _error_response(
                400,
                "MATCH_CONTEXT_MISSING",
                "比赛登录上下文已失效，请重新绑定比赛并重新登录",
            )
        if upstream.code in {"INVALID_MATCH", "INVALID_TOKEN"}:
            return _error_response(400, upstream.code, upstream.message)
        return _error_response(502, upstream.code or "UPSTREAM_ERROR", upstream.message)
    if _caused_by(error, PlatformNotConfigured) is not None:
        return _error_response(503, "PLATFORM_NOT_CONFIGURED", "请先绑定 i春秋比赛")
    if isinstance(error, (ValueError, ConfigError)):
        return _error_response(400, "INVALID_PLATFORM_INPUT", str(error))
    LOGGER.error(
        "Unexpected iChunQiu failure (exception chain: %s)",
        " <- ".join(_exception_type_chain(error)),
    )
    return _error_response(502, "PLATFORM_UNAVAILABLE", "i春秋平台暂时不可用")


def _match_source_id(match: Mapping[str, Any]) -> str:
    for key in ("s_source_id", "s_sso_id"):
        value = str(match.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _login_result_status(status: AuthStatus) -> AuthStatus:
    if status.authenticated:
        return status
    return AuthStatus(True, message="已登录，队伍信息暂不可用")


def _runtime_login_status(
    status: AuthStatus, config_store: RuntimeConfigStore
) -> AuthStatus:
    token = config_store.get().platform_token.strip()
    if not _is_login_token(token):
        if _is_source_context_error(status.message):
            return AuthStatus(False, message="尚未登录")
        return status
    if status.error_code in {"8", "116", "203"} or _is_source_context_error(
        status.message
    ):
        return AuthStatus(True, message="已登录，队伍信息暂不可用")
    return status


def _is_source_context_error(message: str) -> bool:
    return "s_source_id" in message


def _bind_match_with_recovery(
    agent: AgentProtocol,
    match_url: str,
    requested_token: str,
    saved_token: str,
) -> BindResult:
    # Anonymous tokens are issued for one dynamic match context.  Reusing
    # one after a new SSO detail request pairs the token with the wrong ``k``
    # and causes the platform to report a missing ``s_source_id``.  Only a
    # real logged-in session is safe to reuse during a rebind.
    reusable_saved_token = saved_token if _is_login_token(saved_token) else ""
    effective_token = requested_token or reusable_saved_token
    try:
        result = agent.bind_match(match_url, effective_token)
        if (
            not requested_token
            and _is_login_token(saved_token)
            and not result.authenticated
        ):
            LOGGER.warning(
                "Saved iChunQiu login session has no valid match context; requesting anonymous session"
            )
            return agent.bind_match(match_url, "")
        return result
    except Exception as error:
        if requested_token or not saved_token:
            raise
        LOGGER.warning(
            "Saved iChunQiu session could not be reused; requesting a fresh "
            "anonymous session (exception chain: %s)",
            " <- ".join(_exception_type_chain(error)),
        )
        return agent.bind_match(match_url, "")


def _refresh_anonymous_match_context(
    agent: AgentProtocol, config_store: RuntimeConfigStore
) -> BindResult:
    current = config_store.get()
    if not current.match_url:
        raise PlatformNotConfigured("比赛尚未绑定")
    result = agent.bind_match(current.match_url, "")
    config_store.update(
        {
            "match_url": result.match_url,
            "match_key": result.match_key,
            "platform_token": result.platform_token,
            "match_title": str(result.match.get("title", "") or ""),
            "match_login_type": int(result.match.get("login_type", 0) or 0),
            "match_source_id": _match_source_id(result.match),
        }
    )
    agent.invalidate()
    return result


def _logout_and_refresh_context(
    agent: AgentProtocol, config_store: RuntimeConfigStore
) -> Config:
    current = config_store.get()
    if current.platform_token:
        try:
            agent.logout()
        except Exception as error:
            LOGGER.warning(
                "iChunQiu upstream logout failed; clearing local login anyway "
                "(exception chain: %s)",
                " <- ".join(_exception_type_chain(error)),
            )
    if current.match_url:
        try:
            _refresh_anonymous_match_context(agent, config_store)
            return config_store.get()
        except Exception as error:
            LOGGER.warning(
                "iChunQiu anonymous context refresh failed after logout; "
                "clearing platform binding (exception chain: %s)",
                " <- ".join(_exception_type_chain(error)),
            )
    updated = config_store.update(
        {
            "match_url": "",
            "match_key": "",
            "platform_token": "",
            "match_title": "",
            "match_login_type": 0,
            "match_source_id": "",
        }
    )
    agent.invalidate()
    return updated


def _is_login_token(token: str) -> bool:
    return token.strip().startswith("login:")


def _exception_type_chain(error: BaseException) -> tuple[str, ...]:
    names: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return tuple(names)


async def _service_response(
    method: Callable[..., Any], *args: Any, data: Any = None
) -> JSONResponse:
    try:
        result = await asyncio.to_thread(method, *args)
        if data is not None:
            result = data if result is None else result
        return _success_response(result)
    except Exception as exc:
        upstream = _caused_by(exc, APIError)
        if upstream is not None:
            return _platform_service_error(upstream)
        if _caused_by(exc, ModelNotConfigured) is not None:
            return _error_response(503, "MODEL_NOT_CONFIGURED", "OpenAI 兼容模型未配置")
        if _caused_by(exc, PlatformNotConfigured) is not None:
            return _error_response(503, "PLATFORM_NOT_CONFIGURED", "请先绑定 i春秋比赛")
        return _error_response(500, "INTERNAL_ERROR", "服务暂时不可用")


async def _exercise_detail_response(
    services: Services, exercise_id: int, refresh: bool
) -> JSONResponse:
    try:
        result = await asyncio.to_thread(services.agent.exercise, exercise_id, refresh)
        data = _wire(result)
        if services.attachments is not None and isinstance(data, dict):
            try:
                infos = await asyncio.to_thread(
                    services.attachments.attachments, exercise_id
                )
            except Exception:
                infos = []
            _merge_attachment_metadata(data, infos)
        return _success_response(data)
    except Exception as exc:
        upstream = _caused_by(exc, APIError)
        if upstream is not None:
            return _platform_service_error(upstream)
        if _caused_by(exc, PlatformNotConfigured) is not None:
            return _error_response(503, "PLATFORM_NOT_CONFIGURED", "请先绑定 i春秋比赛")
        return _error_response(500, "INTERNAL_ERROR", "服务暂时不可用")


def _platform_service_error(error: APIError) -> JSONResponse:
    if error.status == 429 or error.code == "429":
        return _error_response(429, "RATE_LIMITED", "请求过于频繁，请稍后重试")
    if error.code in {"112", "114", "130", "1002"}:
        return _error_response(
            401,
            "AUTH_EXPIRED",
            "i春秋登录态已过期，请重新登录",
            {"X-Upstream-Error": error.code},
        )
    if error.code == "8" and "s_source_id" in error.message:
        return _error_response(
            400,
            "MATCH_CONTEXT_MISSING",
            "比赛登录上下文已失效，请重新绑定比赛并重新登录",
        )
    return _error_response(502, error.code or "UPSTREAM_ERROR", error.message)


def _merge_attachment_metadata(data: dict[str, Any], infos: Any) -> None:
    attachment = data.get("attachment")
    files = attachment.get("files") if isinstance(attachment, dict) else None
    if not isinstance(files, list) or not isinstance(infos, list):
        return
    by_index = {getattr(item, "index", -1): item for item in infos}
    for index, file in enumerate(files):
        info = by_index.get(index)
        if not isinstance(file, dict) or info is None:
            continue
        file["size"] = max(0, int(getattr(info, "size", 0)))
        file["exists"] = bool(getattr(info, "exists", False))
        file["localPath"] = str(getattr(info, "path", ""))


async def _attachment_response(method: Callable[..., Any], *args: Any) -> JSONResponse:
    try:
        result = await asyncio.to_thread(method, *args)
        return _success_response(result)
    except Exception as exc:
        upstream = _caused_by(exc, APIError)
        if upstream is not None:
            return _platform_service_error(upstream)
        if _caused_by(exc, PlatformNotConfigured) is not None:
            return _error_response(503, "PLATFORM_NOT_CONFIGURED", "请先绑定 i春秋比赛")
        if _caused_by(exc, ExerciseNotFound):
            return _error_response(404, "EXERCISE_NOT_FOUND", "未找到对应题目")
        if _caused_by(exc, AttachmentNotFound):
            return _error_response(404, "ATTACHMENT_NOT_FOUND", "未找到对应附件")
        if _caused_by(exc, InvalidURL):
            return _error_response(
                422, "INVALID_ATTACHMENT_URL", "附件地址无效或不受支持"
            )
        if _caused_by(exc, TooLarge):
            return _error_response(
                413, "ATTACHMENT_TOO_LARGE", "附件超过 512 MiB 下载限制"
            )
        if _caused_by(exc, DownloadFailed):
            return _error_response(
                502, "ATTACHMENT_DOWNLOAD_FAILED", "从平台下载附件失败"
            )
        if _caused_by(exc, StorageError):
            return _error_response(500, "ATTACHMENT_STORAGE_FAILED", "附件写入本地失败")
        return _error_response(500, "INTERNAL_ERROR", "服务暂时不可用")


def _codex_error(error: Exception) -> JSONResponse:
    if _caused_by(error, CodexUnavailable):
        return _error_response(503, "CODEX_UNAVAILABLE", "Codex CLI 未配置或不可用")
    if _caused_by(error, TaskNotFound):
        return _error_response(404, "CODEX_TASK_NOT_FOUND", "Codex 任务不存在")
    if _caused_by(error, ErrPrompt):
        return _error_response(500, "CODEX_PROMPT_FAILED", "Codex 任务提示词准备失败")
    if _caused_by(error, CodexError):
        return _error_response(409, "CODEX_TASK_CONFLICT", str(error))
    return _error_response(500, "CODEX_TASK_FAILED", "Codex 任务暂时不可用")


def _success_response(data: Any, status_code: int = 200) -> JSONResponse:
    content: dict[str, Any] = {"ok": True}
    if data is not None:
        content["data"] = _wire(data)
    return JSONResponse(
        status_code=status_code, content=content, headers={"Cache-Control": "no-store"}
    )


def _error_response(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    merged = {"Cache-Control": "no-store"}
    if headers:
        merged.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"code": code, "message": message}},
        headers=merged,
    )


def _wire(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def _method(request: Request, expected: str) -> bool:
    return request.method == expected


def _method_error(expected: str) -> JSONResponse:
    return _error_response(
        405, "METHOD_NOT_ALLOWED", "请求方法不受支持", {"Allow": expected}
    )


def _parse_id(raw: str) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= MAX_GO_INT64 else None


def _valid_task_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def _start_attachment_download(
    tasks: AttachmentTasksProtocol, request: AttachmentDownloadRequest
) -> dict[str, Any]:
    if request.scope == "all":
        return tasks.start_all()
    if request.scope == "category":
        assert request.category is not None
        return tasks.start_category(request.category)
    assert request.exercise_id is not None
    if request.scope == "exercise":
        return tasks.start_exercise(request.exercise_id)
    if request.scope == "redownload":
        return tasks.start_redownload(request.exercise_id)
    assert request.attachment_index is not None
    return tasks.start_single(request.exercise_id, request.attachment_index)


def _flag_validation_error(error: ValidationError) -> tuple[str, str]:
    """Keep malformed JSON separate from valid JSON with an invalid flag."""

    details = error.errors()
    if len(details) == 1:
        detail = details[0]
        location = tuple(detail.get("loc", ()))
        error_type = str(detail.get("type", ""))
        if location == ("flag",) and error_type in {"missing", "string_too_short"}:
            return "INVALID_FLAG", "Flag 不能为空"
        if location == ("flag",) and error_type == "string_too_long":
            return "INVALID_FLAG", "Flag 不能超过 256 个字符"
    return "INVALID_JSON", "Flag 请求格式无效"


def _refresh(request: Request) -> bool:
    value = request.query_params.get("refresh", "").strip()
    return value == "1" or value.lower() == "true"


def _trusted_attachment_request(request: Request) -> bool:
    return request.headers.get(ATTACHMENT_ACTION_HEADER) == ATTACHMENT_ACTION_PURPOSE


def _trusted_tools_request(request: Request) -> bool:
    return request.headers.get(ATTACHMENT_ACTION_HEADER) == TOOLS_ACTION_PURPOSE


def _trusted_platform_request(request: Request) -> bool:
    return request.headers.get(ATTACHMENT_ACTION_HEADER) == PLATFORM_ACTION_PURPOSE


async def _empty_body_error(request: Request) -> JSONResponse | None:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            return _error_response(400, "INVALID_REQUEST", "请求体无效")
        body.extend(chunk)
    if body.strip():
        return _error_response(400, "UNTRUSTED_INPUT", "此操作不接受请求参数")
    return None


def _has_attachment_manager(
    value: object | None,
) -> TypeGuard[AttachmentManagerProtocol]:
    return value is not None and all(
        hasattr(value, name)
        for name in (
            "catalog",
            "download_all",
            "download_exercise",
            "clear_exercise",
            "redownload_exercise",
        )
    )


E = TypeVar("E", bound=BaseException)


def _caused_by(error: BaseException, expected: type[E]) -> E | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, expected):
            return current
        current = current.__cause__ or current.__context__
    return None


def NewHandler(
    agent: AgentProtocol,
    downloader: DownloaderProtocol | AttachmentManagerProtocol | None = None,
) -> FastAPI:
    return create_app(agent, downloader)


def NewHandlerWithServices(
    agent: AgentProtocol,
    downloader: DownloaderProtocol | AttachmentManagerProtocol | None = None,
    ai: AIProtocol | None = None,
    attachments: AttachmentManagerProtocol | None = None,
) -> FastAPI:
    return create_app(agent, downloader, ai, None, attachments)


def NewHandlerWithServicesAndCodex(
    agent: AgentProtocol,
    downloader: DownloaderProtocol | AttachmentManagerProtocol | None = None,
    ai: AIProtocol | None = None,
    codex: CodexProtocol | None = None,
    attachments: AttachmentManagerProtocol | None = None,
) -> FastAPI:
    return create_app(agent, downloader, ai, codex, attachments)
