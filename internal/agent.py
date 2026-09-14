"""View models shared by the iChunQiu adapter and the local web console."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class APIError(RuntimeError):
    """An upstream HTTP or application-level error."""

    def __init__(self, code: str, message: str, status: int = 0) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}" if code else message)


class PlatformNotConfigured(RuntimeError):
    """Raised when an iChunQiu match has not been bound yet."""


@dataclass(frozen=True)
class MatchInfo:
    note: str = ""
    rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"note": self.note, "rule": self.rule}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "MatchInfo":
        data = value or {}
        return cls(str(data.get("note", "") or ""), str(data.get("rule", "") or ""))


@dataclass(frozen=True)
class Overview:
    stage_point: float = 0.0
    stage_rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"stagePoint": self.stage_point, "stageRank": self.stage_rank}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Overview":
        data = value or {}
        return cls(_as_float(data.get("stagePoint")), _as_int(data.get("stageRank")))


@dataclass(frozen=True)
class ExerciseSummary:
    id: int = 0
    name: str = ""
    order: int = 0
    is_open: bool = False
    has_solved: bool = False
    has_attachment: bool = False
    has_environment: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "order": self.order,
            "isOpen": self.is_open,
            "hasSolved": self.has_solved,
            "hasAttachment": self.has_attachment,
            "hasEnvironment": self.has_environment,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "ExerciseSummary":
        data = value or {}
        return cls(
            _as_int(data.get("id")),
            str(data.get("name", "") or ""),
            _as_int(data.get("order")),
            bool(data.get("isOpen", False)),
            bool(data.get("hasSolved", False)),
            bool(data.get("hasAttachment", False)),
            bool(data.get("hasEnvironment", False)),
        )


@dataclass(frozen=True)
class ExerciseGroup:
    id: int = 0
    name: str = ""
    order: int = 0
    corpus: tuple[ExerciseSummary, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "order": self.order,
            "corpus": [item.to_dict() for item in self.corpus],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "ExerciseGroup":
        data = value or {}
        raw = data.get("corpus")
        corpus = (
            tuple(
                ExerciseSummary.from_dict(item)
                for item in raw
                if isinstance(item, Mapping)
            )
            if isinstance(raw, list)
            else ()
        )
        return cls(
            _as_int(data.get("id")),
            str(data.get("name", "") or ""),
            _as_int(data.get("order")),
            corpus,
        )


@dataclass(frozen=True)
class File:
    name: str = ""
    url: str = ""
    ext: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "url": self.url, "ext": self.ext}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "File":
        data = value or {}
        url = data.get("url") or data.get("previewUrl") or ""
        ext = data.get("ext") or data.get("extension") or ""
        return cls(str(data.get("name", "") or ""), str(url), str(ext))


@dataclass(frozen=True)
class Attachment:
    files: tuple[File, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"files": [item.to_dict() for item in self.files]}

    @classmethod
    def from_wire(cls, value: Any) -> "Attachment":
        if value is None:
            return cls()
        if isinstance(value, list):
            return cls(
                tuple(
                    File.from_dict(item) for item in value if isinstance(item, Mapping)
                )
            )
        if not isinstance(value, Mapping):
            return cls()
        raw_files = value.get("files")
        if raw_files is None:
            candidate = File.from_dict(value)
            if not (candidate.name or candidate.url or candidate.ext):
                return cls()
            return cls((candidate,))
        if not isinstance(raw_files, list):
            return cls()
        return cls(
            tuple(
                File.from_dict(item) for item in raw_files if isinstance(item, Mapping)
            )
        )


@dataclass(frozen=True)
class Credential:
    username: str = ""
    password: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"username": self.username, "password": self.password}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Credential":
        data = value or {}
        return cls(
            str(data.get("username", "") or ""), str(data.get("password", "") or "")
        )


@dataclass(frozen=True)
class PortMapping:
    type: str = ""
    port: str = ""
    proxy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "port": self.port, "proxy": self.proxy}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "PortMapping":
        data = value or {}
        return cls(
            str(data.get("type", "") or ""),
            str(data.get("port", "") or ""),
            str(data.get("proxy", "") or ""),
        )


@dataclass(frozen=True)
class Endpoint:
    expose_ips: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    users: tuple[Credential, ...] = ()
    port_mappings: tuple[PortMapping, ...] = ()
    proxy_ips: tuple[str, ...] = ()
    is_proxy: bool = False
    expire_time: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "exposeIps": list(self.expose_ips),
            "ports": list(self.ports),
            "users": [item.to_dict() for item in self.users],
            "portMappings": [item.to_dict() for item in self.port_mappings],
            "proxyIps": list(self.proxy_ips),
            "isProxy": self.is_proxy,
            "expireTime": self.expire_time,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Endpoint":
        data = value or {}
        return cls(
            _as_str_tuple(data.get("exposeIps")),
            _as_str_tuple(data.get("ports")),
            tuple(
                Credential.from_dict(item)
                for item in data.get("users", [])
                if isinstance(item, Mapping)
            ),
            tuple(
                PortMapping.from_dict(item)
                for item in data.get("portMappings", [])
                if isinstance(item, Mapping)
            ),
            _as_str_tuple(data.get("proxyIps")),
            bool(data.get("isProxy", False)),
            _as_int(data.get("expireTime")),
        )


@dataclass(frozen=True)
class ExerciseDetail:
    id: int = 0
    name: str = ""
    description: str = ""
    has_solved: bool = False
    score: str = ""
    difficulty: str = ""
    attachment: Attachment = field(default_factory=Attachment)
    endpoints: tuple[Endpoint, ...] = ()
    can_refresh_endpoint: bool = False
    is_need_init: bool = False
    endpoint_type: str = ""
    current_time: int = 0
    is_need_check: bool = False
    category: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "hasSolved": self.has_solved,
            "score": self.score,
            "difficulty": self.difficulty,
            "attachment": self.attachment.to_dict(),
            "endpoints": [item.to_dict() for item in self.endpoints],
            "canRefreshEndpoint": self.can_refresh_endpoint,
            "isNeedInit": self.is_need_init,
            "endpointType": self.endpoint_type,
            "currentTime": self.current_time,
            "isNeedCheck": self.is_need_check,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "ExerciseDetail":
        data = value or {}
        raw_endpoints = data.get("endpoints")
        endpoints = (
            tuple(
                Endpoint.from_dict(item)
                for item in raw_endpoints
                if isinstance(item, Mapping)
            )
            if isinstance(raw_endpoints, list)
            else ()
        )
        return cls(
            _as_int(data.get("id")),
            str(data.get("name", "") or ""),
            str(data.get("description", "") or ""),
            bool(data.get("hasSolved", False)),
            str(data.get("score", "") or ""),
            str(data.get("difficulty", "") or ""),
            Attachment.from_wire(data.get("attachment")),
            endpoints,
            bool(data.get("canRefreshEndpoint", False)),
            bool(data.get("isNeedInit", False)),
            str(data.get("endpointType", "") or ""),
            _as_int(data.get("currentTime")),
            bool(data.get("isNeedCheck", False)),
            str(data.get("category", "") or ""),
        )


@dataclass(frozen=True)
class AnswerResult:
    is_correct: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"isCorrect": self.is_correct}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "AnswerResult":
        return cls(bool((value or {}).get("isCorrect", False)))


@dataclass(frozen=True)
class Notice:
    id: int = 0
    title: str = ""
    content: str = ""
    created_at: str = ""
    created_time: int = 0
    user_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "createdAt": self.created_at,
            "createdTime": self.created_time,
            "userName": self.user_name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Notice":
        data = value or {}
        return cls(
            _as_int(data.get("id")),
            str(data.get("title", "") or ""),
            str(data.get("content", "") or ""),
            str(data.get("createdAt", "") or ""),
            _as_int(data.get("createdTime")),
            str(data.get("userName", "") or ""),
        )


@dataclass(frozen=True)
class NoticeDetail:
    id: int = 0
    title: str = ""
    content: str = ""
    is_file: bool = False
    file: Attachment = field(default_factory=Attachment)
    created_time: int = 0
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "isFile": self.is_file,
            "file": self.file.to_dict(),
            "createdTime": self.created_time,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "NoticeDetail":
        data = value or {}
        return cls(
            _as_int(data.get("id")),
            str(data.get("title", "") or ""),
            str(data.get("content", "") or ""),
            bool(data.get("isFile", False)),
            Attachment.from_wire(data.get("file")),
            _as_int(data.get("createdTime")),
            str(data.get("url", "") or ""),
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


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item is not None)
