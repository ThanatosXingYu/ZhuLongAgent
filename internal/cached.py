"""Thread-safe in-memory caches with platform rate-limit retries."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Literal, Protocol, TypeVar, cast

from .agent import (
    APIError,
    AnswerResult,
    ExerciseDetail,
    ExerciseGroup,
    MatchInfo,
    Notice,
    NoticeDetail,
    Overview,
)
from .ichunqiu_api import AuthStatus, BindResult, CaptchaImage, LoginResult

MAX_RATE_LIMIT_RETRIES = 5
RETRY_BASE_DELAY = 0.250

T = TypeVar("T")
K = TypeVar("K")


class SourceProtocol(Protocol):
    def bind_match(self, match_url: str, supplied_token: str = "") -> BindResult: ...

    def auth_status(self) -> AuthStatus: ...

    def schema_diagnostics(self) -> dict[str, object]: ...

    def login_password(
        self, account: str, password: str, image_code: str = ""
    ) -> LoginResult: ...

    def send_sms_code(self, area_id: str, phone: str, rotation: int) -> None: ...

    def login_sms(self, phone: str, sms_code: str) -> LoginResult: ...

    def logout(self) -> None: ...

    def captcha(self, kind: Literal["image", "rotate"]) -> CaptchaImage: ...

    def invalidate(self) -> None: ...

    def match_info(self, refresh: bool = False) -> MatchInfo: ...

    def overview(self, refresh: bool = False) -> Overview: ...

    def exercises(self, refresh: bool = False) -> list[ExerciseGroup]: ...

    def exercise(self, exercise_id: int, refresh: bool = False) -> ExerciseDetail: ...

    def attachment_detail(
        self, exercise_id: int, refresh: bool = False
    ) -> ExerciseDetail: ...

    def submit_flag(self, exercise_id: int, flag: str) -> AnswerResult: ...

    def build_environment(self, exercise_id: int) -> None: ...

    def recover_environment(self, exercise_id: int) -> None: ...

    def notices(self, refresh: bool = False) -> list[Notice]: ...

    def notice(self, notice_id: int, refresh: bool = False) -> NoticeDetail: ...


class _ValueCache(Generic[T]):
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._value: T | None = None
        self._valid = False
        self._loading = False
        self._last_value: T | None = None
        self._last_error: BaseException | None = None

    def get(self, refresh: bool, load: Callable[[], T]) -> T:
        with self._condition:
            if self._valid and not refresh:
                if self._value is None:
                    raise RuntimeError("cache value is unexpectedly empty")
                return self._value
            if self._loading:
                while self._loading:
                    self._condition.wait()
                if self._last_error is not None:
                    raise self._last_error
                if self._last_value is None:
                    raise RuntimeError("cache load completed without a value")
                return self._last_value
            self._loading = True
            self._last_error = None
        try:
            value = load()
        except Exception as exc:
            with self._condition:
                self._last_error = exc
                self._loading = False
                self._condition.notify_all()
            raise
        with self._condition:
            self._value = value
            self._last_value = value
            self._valid = True
            self._loading = False
            self._last_error = None
            self._condition.notify_all()
        return value


class _KeyedCache(Generic[K, T]):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[K, _ValueCache[T]] = {}

    def get(self, key: K, refresh: bool, load: Callable[[], T]) -> T:
        with self._lock:
            slot = self._items.setdefault(key, _ValueCache[T]())
        return slot.get(refresh, load)


@dataclass
class CachedService:
    source: SourceProtocol
    wait: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._cache_lock = threading.Lock()
        self._reset_caches()

    def _reset_caches(self) -> None:
        self._match_info: _ValueCache[MatchInfo] = _ValueCache()
        self._overview: _ValueCache[Overview] = _ValueCache()
        self._exercises: _ValueCache[list[ExerciseGroup]] = _ValueCache()
        self._exercise: _KeyedCache[int, ExerciseDetail] = _KeyedCache()
        self._notices: _ValueCache[list[Notice]] = _ValueCache()
        self._notice: _KeyedCache[int, NoticeDetail] = _KeyedCache()

    def invalidate(self) -> None:
        self.source.invalidate()
        with self._cache_lock:
            self._reset_caches()

    def bind_match(self, match_url: str, supplied_token: str = "") -> BindResult:
        return self.source.bind_match(match_url, supplied_token)

    def auth_status(self) -> AuthStatus:
        return self.source.auth_status()

    def schema_diagnostics(self) -> dict[str, object]:
        reader = getattr(self.source, "schema_diagnostics", None)
        if not callable(reader):
            return {"changes": [], "interfaces": {}}
        result = reader()
        return result if isinstance(result, dict) else {"changes": [], "interfaces": {}}

    def login_password(
        self, account: str, password: str, image_code: str = ""
    ) -> LoginResult:
        return self.source.login_password(account, password, image_code)

    def send_sms_code(self, area_id: str, phone: str, rotation: int) -> None:
        self.source.send_sms_code(area_id, phone, rotation)

    def login_sms(self, phone: str, sms_code: str) -> LoginResult:
        return self.source.login_sms(phone, sms_code)

    def logout(self) -> None:
        self.source.logout()
        self.invalidate()

    def captcha(self, kind: Literal["image", "rotate"]) -> CaptchaImage:
        return self.source.captcha(kind)

    def match_info(self, refresh: bool = False) -> MatchInfo:
        with self._cache_lock:
            cache = self._match_info
        return cache.get(refresh, lambda: self._retry(self.source.match_info))

    def overview(self, refresh: bool = False) -> Overview:
        with self._cache_lock:
            cache = self._overview
        return cache.get(refresh, lambda: self._retry(self.source.overview))

    def exercises(self, refresh: bool = False) -> list[ExerciseGroup]:
        with self._cache_lock:
            cache = self._exercises
        return cache.get(refresh, lambda: self._retry(self.source.exercises))

    def exercise(self, exercise_id: int, refresh: bool = False) -> ExerciseDetail:
        with self._cache_lock:
            cache = self._exercise
        return cache.get(
            exercise_id,
            refresh,
            lambda: self._retry(lambda: self.source.exercise(exercise_id)),
        )

    def attachment_detail(
        self, exercise_id: int, refresh: bool = False
    ) -> ExerciseDetail:
        loader = getattr(self.source, "attachment_detail", None)
        if not callable(loader):
            return self.exercise(exercise_id, refresh)
        return cast(ExerciseDetail, self._retry(lambda: loader(exercise_id, refresh)))

    def submit_flag(self, exercise_id: int, flag: str) -> AnswerResult:
        result = self.source.submit_flag(exercise_id, flag)
        self.invalidate()
        return result

    def build_environment(self, exercise_id: int) -> None:
        self.source.build_environment(exercise_id)
        with self._cache_lock:
            self._exercise = _KeyedCache()

    def recover_environment(self, exercise_id: int) -> None:
        self.source.recover_environment(exercise_id)
        with self._cache_lock:
            self._exercise = _KeyedCache()

    def notices(self, refresh: bool = False) -> list[Notice]:
        with self._cache_lock:
            cache = self._notices
        return cache.get(refresh, lambda: self._retry(self.source.notices))

    def notice(self, notice_id: int, refresh: bool = False) -> NoticeDetail:
        with self._cache_lock:
            cache = self._notice
        return cache.get(
            notice_id,
            refresh,
            lambda: self._retry(lambda: self.source.notice(notice_id)),
        )

    def _retry(self, loader: Callable[[], T]) -> T:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                return loader()
            except Exception as exc:
                api_error = _caused_by(exc, APIError)
                if (
                    api_error is None
                    or not _is_rate_limit(api_error)
                    or attempt >= MAX_RATE_LIMIT_RETRIES
                ):
                    raise
                self.wait(RETRY_BASE_DELAY * (attempt + 1))
        raise RuntimeError("unreachable retry state")


def _is_rate_limit(error: APIError) -> bool:
    return error.status == 429 or error.code.strip() in {"429", "40001"}


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


Service = CachedService
New = CachedService
