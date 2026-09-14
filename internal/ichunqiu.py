"""iChunQiu adapter that preserves the workbench's existing view models."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from .agent import (
    APIError,
    AnswerResult,
    Attachment,
    Credential,
    Endpoint,
    ExerciseDetail,
    ExerciseGroup,
    ExerciseSummary,
    File,
    MatchInfo,
    Notice,
    NoticeDetail,
    Overview,
    PortMapping,
)
from .ichunqiu_api import (
    AuthStatus,
    BindResult,
    CaptchaImage,
    ConfigProvider,
    IChunQiuAPI,
    LoginResult,
    Transport,
)
from .ichunqiu_crypto import MATCH_ORIGIN, parse_match_url

MAX_QUESTION_COUNT = 10_000
PAGE_SIZE = 100
EMPTY_IMAGE_PLACEHOLDER = "f97fae4e1ffdb1ee3b65231aa693ea85"
MATCH_STATE_CODES = frozenset({"201", "202", "203"})
MATCH_TIMEZONE = ZoneInfo("Asia/Shanghai")
NOTICE_TYPE = 1


class AgentClient:
    """Translate iChunQiu data into the original local web API contract."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        config_provider: ConfigProvider,
        transport: Transport | None = None,
        schema_path: str | Path | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._api = IChunQiuAPI(
            config_provider,
            timeout=timeout,
            transport=transport,
            schema_path=schema_path,
        )
        self._lock = threading.RLock()
        self._question_by_id: dict[int, Mapping[str, Any]] = {}
        self._question_id_by_platform: dict[str, int] = {}
        self._group_id_by_platform: dict[str, int] = {}
        self._notice_by_id: dict[int, Mapping[str, Any]] = {}
        self._notice_id_by_platform: dict[str, int] = {}
        self._pending_environments: dict[int, str] = {}
        self._opened_platform_questions: set[str] = set()

    def bind_match(self, match_url: str, supplied_token: str = "") -> BindResult:
        return self._api.bind_match(match_url, supplied_token)

    def auth_status(self) -> AuthStatus:
        return self._api.auth_status()

    def schema_diagnostics(self) -> dict[str, Any]:
        return self._api.schema_diagnostics()

    def login_password(
        self, account: str, password: str, image_code: str = ""
    ) -> LoginResult:
        return self._api.login_password(account, password, image_code)

    def send_sms_code(self, area_id: str, phone: str, rotation: int) -> None:
        self._api.send_sms_code(area_id, phone, rotation)

    def login_sms(self, phone: str, sms_code: str) -> LoginResult:
        return self._api.login_sms(phone, sms_code)

    def logout(self) -> None:
        self._api.logout()

    def captcha(self, kind: Literal["image", "rotate"]) -> CaptchaImage:
        return self._api.captcha(kind)

    def invalidate(self) -> None:
        with self._lock:
            self._question_by_id.clear()
            self._notice_by_id.clear()
            self._pending_environments.clear()
            self._opened_platform_questions.clear()

    def match_info(self, _refresh: bool = False) -> MatchInfo:
        config = self._config_provider()
        address = parse_match_url(config.match_url)
        if address.event_slug and not config.platform_token.startswith("login:"):
            try:
                data = _mapping(
                    self._api.post(
                        "/match/sso/detail",
                        {"url_key": address.event_slug},
                        match_key="",
                        token="",
                    )
                )
            except APIError:
                data = _mapping(
                    self._api.post(
                        "/match/detail",
                        {"url_key": config.match_key, "is_encrypt": 1},
                    )
                )
        else:
            data = _mapping(
                self._api.post(
                    "/match/detail",
                    {"url_key": config.match_key, "is_encrypt": 1},
                )
            )
        title = str(data.get("title", "") or "")
        start_time = str(data.get("start_time", "") or "")
        end_time = str(data.get("end_time", "") or "")
        note_lines = [
            line for line in (title, _time_range(start_time, end_time)) if line
        ]
        match_status = (
            "已结束"
            if _as_int(data.get("match_status")) == 203
            else self._match_status(start_time, end_time)
        )
        rule_lines = [
            f"比赛状态：{match_status}",
            f"参赛方式：{_entry_label(data.get('entry_type'))}",
            f"登录方式：{_login_label(data.get('login_type'))}",
        ]
        return MatchInfo("\n".join(note_lines), "\n".join(rule_lines))

    def overview(self, _refresh: bool = False) -> Overview:
        data = _mapping(self._api.post("/match/team/info", {"team_id": ""}))
        return Overview(
            _as_float(data.get("total_score")), _as_int(data.get("team_rank"))
        )

    def exercises(self, _refresh: bool = False) -> list[ExerciseGroup]:
        rows = self._load_questions()
        groups: OrderedDict[str, list[ExerciseSummary]] = OrderedDict()
        group_names: dict[str, str] = {}
        for order, row in enumerate(rows, 1):
            local_id = self._local_question_id(row)
            if local_id is None:
                continue
            category_id = str(row.get("category_id", "") or "uncategorized")
            category_name = str(row.get("category", "") or "其他")
            group_names.setdefault(category_id, category_name)
            attachment = _attachments(row)
            groups.setdefault(category_id, []).append(
                ExerciseSummary(
                    id=local_id,
                    name=str(row.get("title", "") or f"题目 {order}"),
                    order=_as_int(row.get("sort")) or order,
                    is_open=bool(row.get("is_allow_solve", True))
                    and not bool(row.get("is_lock", False)),
                    has_solved=_is_solved(row),
                    has_attachment=bool(attachment.files),
                    has_environment=_supports_environment(row),
                )
            )
        result: list[ExerciseGroup] = []
        for order, (category_id, questions) in enumerate(groups.items(), 1):
            result.append(
                ExerciseGroup(
                    id=self._local_group_id(category_id),
                    name=group_names[category_id],
                    order=order,
                    corpus=tuple(questions),
                )
            )
        return result

    def exercise(self, exercise_id: int, refresh: bool = False) -> ExerciseDetail:
        row = self._question(exercise_id, refresh)
        platform_id = str(row.get("id", "") or "")
        can_use_environment = _supports_environment(row) and bool(
            row.get("is_allow_solve", True)
        )
        endpoints: tuple[Endpoint, ...] = ()
        is_need_init = False
        is_need_check = False
        can_refresh = False
        if can_use_environment:
            endpoints, is_need_init, is_need_check, can_refresh = (
                self._environment_state(
                    exercise_id,
                    platform_id,
                )
            )
        return ExerciseDetail(
            id=exercise_id,
            name=str(row.get("title", "") or ""),
            description=str(row.get("content", "") or ""),
            has_solved=_is_solved(row),
            score=str(row.get("score", "") or ""),
            difficulty=str(row.get("difficult", "") or ""),
            attachment=_attachments(row),
            endpoints=endpoints,
            can_refresh_endpoint=can_refresh,
            is_need_init=is_need_init,
            endpoint_type="docker" if can_use_environment else "",
            current_time=time.time_ns() // 1_000_000,
            is_need_check=is_need_check,
            category=str(row.get("category", "") or ""),
        )

    def attachment_detail(
        self, exercise_id: int, refresh: bool = False
    ) -> ExerciseDetail:
        """Return attachment metadata without querying container state."""

        row = self._question(exercise_id, refresh)
        return ExerciseDetail(
            id=exercise_id,
            name=str(row.get("title", "") or ""),
            description=str(row.get("content", "") or ""),
            has_solved=_is_solved(row),
            score=str(row.get("score", "") or ""),
            difficulty=str(row.get("difficult", "") or ""),
            attachment=_attachments(row),
            category=str(row.get("category", "") or ""),
        )

    def submit_flag(self, exercise_id: int, flag: str) -> AnswerResult:
        platform_id = self._platform_question_id(exercise_id)
        self._ensure_question_opened(platform_id)
        self._api.post(
            "/match/red/submit",
            {
                "question_id": platform_id,
                "answer": flag,
                "img_url": EMPTY_IMAGE_PLACEHOLDER,
            },
        )
        return AnswerResult(True)

    def build_environment(self, exercise_id: int) -> None:
        platform_id = self._platform_question_id(exercise_id)
        self._ensure_question_opened(platform_id)
        data = _mapping(
            self._api.post("/match/docker/apply", {"question_id": platform_id})
        )
        environment_id = str(data.get("uuid", "") or "").strip()
        if not environment_id:
            raise APIError("INVALID_ENVIRONMENT", "平台未返回环境标识")
        with self._lock:
            self._pending_environments[exercise_id] = environment_id

    def recover_environment(self, exercise_id: int) -> None:
        platform_id = self._platform_question_id(exercise_id)
        with self._lock:
            environment_id = self._pending_environments.get(exercise_id, "")
        if not environment_id:
            data = _mapping(
                self._api.post("/match/docker/info", {"question_id": platform_id})
            )
            environment_id = str(data.get("uuid", "") or "").strip()
        if not environment_id:
            raise APIError("ENVIRONMENT_NOT_FOUND", "当前题目没有运行中的环境")
        self._api.post(
            "/match/docker/close",
            {"uuid": environment_id, "close_type": 3},
        )
        with self._lock:
            self._pending_environments.pop(exercise_id, None)

    def notices(self, _refresh: bool = False) -> list[Notice]:
        try:
            data = self._api.post(
                "/match/notice/lists",
                {"type": NOTICE_TYPE, "page_index": 1, "page_size": ""},
            )
        except APIError as exc:
            if exc.code == "116":
                return []
            raise
        rows = _list_from_data(data)
        result: list[Notice] = []
        with self._lock:
            self._notice_by_id.clear()
            for order, row in enumerate(rows, 1):
                platform_id = str(
                    row.get("id", "") or row.get("notice_id", "") or order
                )
                local_id = self._stable_id(platform_id, self._notice_id_by_platform)
                self._notice_by_id[local_id] = row
                result.append(_notice_from_row(local_id, row))
        return result

    def notice(self, notice_id: int, refresh: bool = False) -> NoticeDetail:
        if refresh:
            self.notices(True)
        with self._lock:
            row = self._notice_by_id.get(notice_id)
        if row is None:
            self.notices()
            with self._lock:
                row = self._notice_by_id.get(notice_id)
        if row is None:
            raise APIError("NOT_FOUND", "公告不存在")
        notice = _notice_from_row(notice_id, row)
        attachment = _attachments(row, file_keys=("file", "file_url", "file_list"))
        return NoticeDetail(
            id=notice.id,
            title=notice.title,
            content=notice.content,
            is_file=bool(attachment.files),
            file=attachment,
            created_time=notice.created_time,
            url=str(row.get("url", "") or ""),
        )

    def _match_status(self, start_time: str, end_time: str) -> str:
        try:
            self._api.post("/match/surplus_time", {})
        except APIError as exc:
            if exc.code in MATCH_STATE_CODES:
                message = exc.message.strip()
                return message.removeprefix("比赛") or _scheduled_status(
                    start_time, end_time
                )
            return _scheduled_status(start_time, end_time)
        return _scheduled_status(start_time, end_time, active_default=True)

    def _load_questions(self) -> list[Mapping[str, Any]]:
        first = _mapping(
            self._api.post(
                "/match/question/lists",
                {"category_id": "", "page_index": 1, "page_size": PAGE_SIZE},
            )
        )
        rows = _mapping_rows(first.get("lists"))
        total = _as_int(first.get("total")) or len(rows)
        if total > MAX_QUESTION_COUNT:
            raise APIError("TOO_MANY_QUESTIONS", "题目数量超过本地处理上限")
        page_count = math.ceil(total / PAGE_SIZE) if total else 1
        for page_index in range(2, page_count + 1):
            page = _mapping(
                self._api.post(
                    "/match/question/lists",
                    {
                        "category_id": "",
                        "page_index": page_index,
                        "page_size": PAGE_SIZE,
                    },
                )
            )
            rows.extend(_mapping_rows(page.get("lists")))
        with self._lock:
            self._question_by_id.clear()
            for row in rows:
                local_id = self._local_question_id(row)
                if local_id is not None:
                    self._question_by_id[local_id] = row
        return rows

    def _question(self, exercise_id: int, refresh: bool) -> Mapping[str, Any]:
        if refresh:
            self._load_questions()
        with self._lock:
            row = self._question_by_id.get(exercise_id)
        if row is None:
            self._load_questions()
            with self._lock:
                row = self._question_by_id.get(exercise_id)
        if row is None:
            raise APIError("NOT_FOUND", "题目不存在")
        return row

    def _platform_question_id(self, exercise_id: int) -> str:
        row = self._question(exercise_id, False)
        platform_id = str(row.get("id", "") or "").strip()
        if not platform_id:
            raise APIError("NOT_FOUND", "题目不存在")
        return platform_id

    def _local_question_id(self, row: Mapping[str, Any]) -> int | None:
        platform_id = str(row.get("id", "") or "").strip()
        if not platform_id:
            return None
        return self._stable_id(platform_id, self._question_id_by_platform)

    def _local_group_id(self, platform_id: str) -> int:
        return self._stable_id(platform_id, self._group_id_by_platform)

    def _stable_id(self, platform_id: str, values: dict[str, int]) -> int:
        with self._lock:
            current = values.get(platform_id)
            if current is not None:
                return current
            local_id = len(values) + 1
            values[platform_id] = local_id
            return local_id

    def _ensure_question_opened(self, platform_id: str) -> None:
        with self._lock:
            if platform_id in self._opened_platform_questions:
                return
        try:
            self._api.post("/match/question/open_record", {"question_id": platform_id})
        except APIError as exc:
            if exc.code != "203":
                raise
        with self._lock:
            self._opened_platform_questions.add(platform_id)

    def _environment_state(
        self,
        exercise_id: int,
        platform_id: str,
    ) -> tuple[tuple[Endpoint, ...], bool, bool, bool]:
        with self._lock:
            pending = self._pending_environments.get(exercise_id, "")
        if pending:
            process = _mapping(
                self._api.post("/match/docker/process", {"uuid": pending})
            )
            if _as_int(process.get("process")) == 0:
                return (), False, True, False
            with self._lock:
                self._pending_environments.pop(exercise_id, None)
        try:
            data = _mapping(
                self._api.post("/match/docker/info", {"question_id": platform_id})
            )
        except APIError as exc:
            if exc.code in {"101", "116"}:
                return (), True, False, False
            raise
        endpoint = _endpoint_from_data(data)
        if endpoint is None:
            return (), True, False, False
        return (endpoint,), False, False, _as_int(data.get("delay_count")) > 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_rows(value: Any) -> list[Mapping[str, Any]]:
    return (
        [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def _list_from_data(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return _mapping_rows(value)
    return _mapping_rows(_mapping(value).get("lists"))


def _is_solved(row: Mapping[str, Any]) -> bool:
    solved = _mapping(row.get("red_solved"))
    return bool(solved.get("is_solved", False))


def _supports_environment(row: Mapping[str, Any]) -> bool:
    """Return whether iChunQiu explicitly assigned a container image.

    ``is_allow_solve`` only means that the question can be answered.  It does
    not imply that a Docker environment exists.  On iChunQiu's match API the
    red-team image name is the capability marker used by the official client;
    ordinary questions leave it empty.  Keeping this check independent from
    attachment parsing prevents both ordinary and attachment-only questions
    from showing a misleading "启动容器" action.
    """

    image = row.get("red_image")
    return isinstance(image, str) and bool(image.strip())


def _attachments(
    row: Mapping[str, Any],
    *,
    file_keys: tuple[str, ...] = ("file_url", "file_list"),
) -> Attachment:
    files: list[File] = []
    seen: set[str] = set()
    fallback_name = str(row.get("file_name", "") or "")
    for key in file_keys:
        _collect_files(
            row.get(key),
            files,
            seen,
            fallback_name if key in {"file", "file_url"} else "",
        )
    _collect_content_files(row.get("content"), files, seen)
    return Attachment(tuple(files))


class _AttachmentLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = next((value for name, value in attrs if name.lower() == "href"), None)
        if href:
            self.urls.append(href)


def _collect_content_files(value: Any, files: list[File], seen: set[str]) -> None:
    if not isinstance(value, str) or "<a" not in value.lower():
        return
    parser = _AttachmentLinkParser()
    parser.feed(value)
    parser.close()
    for url in parser.urls:
        _append_file(url, "", files, seen)


def _collect_files(
    value: Any, files: list[File], seen: set[str], fallback_name: str = ""
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_files(item, files, seen, fallback_name)
        return
    if isinstance(value, Mapping):
        nested = value.get("files") or value.get("file_list")
        if isinstance(nested, (list, Mapping)):
            _collect_files(nested, files, seen, fallback_name)
        url = value.get("url") or value.get("file_url") or value.get("previewUrl")
        name = (
            value.get("name")
            or value.get("file_name")
            or value.get("title")
            or fallback_name
        )
        if isinstance(url, str):
            _append_file(url, str(name or ""), files, seen)
        return
    if isinstance(value, str):
        _append_file(value, fallback_name, files, seen)


def _append_file(url: str, name: str, files: list[File], seen: set[str]) -> None:
    raw_url = url.strip()
    if not raw_url:
        return
    normalized_url = urljoin(f"{MATCH_ORIGIN}/", raw_url)
    parsed_url = urlsplit(normalized_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        return
    if normalized_url in seen:
        return
    seen.add(normalized_url)
    path_name = PurePosixPath(unquote(parsed_url.path)).name
    file_name = name.strip() or path_name or f"附件 {len(files) + 1}"
    extension = PurePosixPath(file_name).suffix.lstrip(".")
    files.append(File(file_name, normalized_url, extension))


def _endpoint_from_data(data: Mapping[str, Any]) -> Endpoint | None:
    environment_id = str(data.get("uuid", "") or "").strip()
    countdown = _as_int(data.get("countdown"))
    url = str(data.get("url", "") or "").strip()
    ssh = _mapping(data.get("ssh"))
    ssh_url = str(ssh.get("ssh_url", "") or "").strip()
    if not environment_id or countdown <= 0 or not (url or ssh_url):
        return None
    username = str(ssh.get("username", "") or "")
    password = str(ssh.get("password", "") or "")
    users = (Credential(username, password),) if username or password else ()
    mappings = (PortMapping("ssh", "", ssh_url),) if ssh_url else ()
    expire_time = time.time_ns() // 1_000_000 + countdown * 1000
    return Endpoint(
        expose_ips=(url,) if url else (),
        ports=(ssh_url,) if ssh_url else (),
        users=users,
        port_mappings=mappings,
        proxy_ips=(url,) if url else (),
        is_proxy=bool(url),
        expire_time=expire_time,
    )


def _notice_from_row(local_id: int, row: Mapping[str, Any]) -> Notice:
    created = (
        row.get("create_time") or row.get("created_at") or row.get("created_time") or ""
    )
    content = str(row.get("content", "") or row.get("notice_content", "") or "")
    return Notice(
        id=local_id,
        title=_notice_title(row, content),
        content=content,
        created_at=str(created),
        created_time=_timestamp_millis(created),
        user_name=str(row.get("user_name", "") or row.get("admin_name", "") or ""),
    )


def _time_range(start_time: str, end_time: str) -> str:
    if start_time and end_time:
        return f"比赛时间：{start_time} 至 {end_time}"
    if start_time:
        return f"开始时间：{start_time}"
    if end_time:
        return f"结束时间：{end_time}"
    return ""


def _scheduled_status(
    start_time: str, end_time: str, *, active_default: bool = False
) -> str:
    now = datetime.now(MATCH_TIMEZONE)
    start = _parse_match_time(start_time)
    end = _parse_match_time(end_time)
    if start is not None and now < start:
        return "未开始"
    if end is not None and now >= end:
        return "已结束"
    if active_default or start is not None or end is not None:
        return "进行中"
    return "按平台状态"


def _parse_match_time(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=MATCH_TIMEZONE)
        if parsed.tzinfo is None
        else parsed.astimezone(MATCH_TIMEZONE)
    )


def _timestamp_millis(value: Any) -> int:
    numeric = _as_float(value)
    if numeric > 0:
        return int(numeric if numeric >= 1_000_000_000_000 else numeric * 1000)
    parsed = _parse_match_time(str(value or ""))
    return int(parsed.timestamp() * 1000) if parsed is not None else 0


def _notice_title(row: Mapping[str, Any], content: str) -> str:
    explicit = str(row.get("title", "") or row.get("notice_title", "") or "").strip()
    if explicit:
        return explicit
    summary = " ".join(content.split())
    if not summary:
        return "公告"
    return f"{summary[:48]}..." if len(summary) > 48 else summary


def _entry_label(value: Any) -> str:
    return {1: "个人", 2: "团队"}.get(_as_int(value), "按平台设置")


def _login_label(value: Any) -> str:
    return {1: "密码", 2: "短信验证码", 3: "邮箱验证码", 4: "平台账号"}.get(
        _as_int(value),
        "按平台设置",
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


Client = AgentClient
New = AgentClient
