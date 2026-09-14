"""Configuration loading and validation for the Python workbench."""

from __future__ import annotations

import os
import json
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Callable, Mapping
from typing import Any, cast

from .ichunqiu_crypto import MatchURLValidationError, parse_match_url

DEFAULT_LISTEN_ADDR = "127.0.0.1:8080"
DEFAULT_CODEX_MAX_CONCURRENCY = 5
DEFAULT_CODEX_SYSTEM_PROMPT = """你是通过本机 Codex CLI 启动的受限 CTF 解题代理。请只处理这一个题目。
阅读项目中的 docs/api_doc.md，所有竞赛操作都通过已启动的本地 Web API 完成；禁止读取 runtime/config.json 或 .runtime-config.json，禁止直接访问 i春秋上游接口，也不要在终端输出、最终回答、WP 或文件中暴露任何凭据。
如果存在，请先阅读 tools/installed-tools.txt，了解本工作台已经安装的工具、命令入口和基本用法；优先从 tools/ 目录调用工具。解题过程中产生的题目文件、脚本和临时文件必须保存到本题对应的题目工作目录，不能污染其他目录；如确需额外工具，可以从可信来源下载到该题目目录后使用，但不得写入系统目录。
如果需要进行口令审计或候选值分析，可以按需阅读 tools/dictionaries/ 中的内置字典文件；这些字典只作为授权题目的输入参考，不要复制到系统目录，也不要把字典内容写入最终 WP。字典目录和文件名以实际目录为准。
## 解题原则
想一切办法解决题目：可以进行公开网络搜索，查阅公开资料、官方文档、技术文章和公开源码；需要验证结论时应实际运行命令、脚本或请求并记录证据。网络搜索仅用于获取公开资料，不得把搜索结果或未经验证的推测当成 Flag。

## 安全边界
禁止爆破、穷举、猜测、伪造或拼接 Flag；必须从题目附件、授权靶机或明确的接口响应中取得实际证据。禁止攻击 i春秋平台本身、登录接口、比赛接口、网关和其他竞赛基础设施；只允许访问本题明确授权的附件、靶机和本地 Web API，且不得修改与本题无关的项目文件。
Flag 提交时只提交花括号内部的值，平台明确返回正确后才算成功，并回收已启动的靶机。
最终回复必须是一份可复现的中文 Markdown WP，包含实际命令、利用过程、Flag 推导和环境清理结果。"""


class ConfigError(ValueError):
    """Raised when the dotenv file or process environment is invalid."""


@dataclass(frozen=True)
class Config:
    match_url: str = ""
    match_key: str = ""
    platform_token: str = ""
    match_title: str = ""
    match_login_type: int = 0
    match_source_id: str = ""
    listen_addr: str = DEFAULT_LISTEN_ADDR
    model_base_url: str = ""
    model_api_key: str = ""
    model_name: str = ""
    codex_base_url: str = ""
    codex_api_key: str = ""
    codex_model: str = ""
    codex_max_concurrency: int = DEFAULT_CODEX_MAX_CONCURRENCY
    codex_system_prompt: str = ""
    codex_ctf_skills_enabled: bool = True
    codex_auto_resume_interrupted: bool = False


def load(path: str | Path = ".env") -> Config:
    """Load a config file and apply uppercase/lowercase env overrides."""

    values = _read_env_file(Path(path))
    listen_addr = _resolve(values, "LISTENADDR", "listenaddr") or DEFAULT_LISTEN_ADDR
    return Config(listen_addr=listen_addr)


class RuntimeConfigStore:
    """Thread-safe runtime configuration with workspace-local persistence."""

    def __init__(
        self, initial: Config | None = None, path: str | Path = ".runtime-config.json"
    ) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Config], None]] = []
        self._config = initial or Config()
        self._load_persisted()

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> Config:
        with self._lock:
            return self._config

    def subscribe(self, listener: Callable[[Config], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def update(self, values: Mapping[str, Any]) -> Config:
        with self._lock:
            current = self._config
            candidate = Config(
                match_url=_value(values, "match_url", current.match_url),
                match_key=_value(values, "match_key", current.match_key),
                platform_token=_value(values, "platform_token", current.platform_token),
                match_title=_value(values, "match_title", current.match_title),
                match_login_type=_int_value(
                    values, "match_login_type", current.match_login_type
                ),
                match_source_id=_value(
                    values, "match_source_id", current.match_source_id
                ),
                listen_addr=current.listen_addr,
                model_base_url=_value(values, "model_base_url", current.model_base_url),
                model_api_key=_value(values, "model_api_key", current.model_api_key),
                model_name=_value(values, "model_name", current.model_name),
                codex_base_url=_value(values, "codex_base_url", current.codex_base_url),
                codex_api_key=_value(values, "codex_api_key", current.codex_api_key),
                codex_model=_value(values, "codex_model", current.codex_model),
                codex_max_concurrency=_int_value(
                    values, "codex_max_concurrency", current.codex_max_concurrency
                ),
                codex_system_prompt=_value(
                    values, "codex_system_prompt", current.codex_system_prompt
                ),
                codex_ctf_skills_enabled=_bool_value(
                    values, "codex_ctf_skills_enabled", current.codex_ctf_skills_enabled
                ),
                codex_auto_resume_interrupted=_bool_value(
                    values,
                    "codex_auto_resume_interrupted",
                    current.codex_auto_resume_interrupted,
                ),
            )
            validate_runtime_config(candidate)
            self._persist(candidate)
            self._config = candidate
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(candidate)
        return candidate

    def public(self) -> dict[str, Any]:
        config = self.get()
        return {
            "configured": bool(
                config.match_url and config.match_key and config.platform_token
            ),
            "matchUrl": _public_match_url(config.match_url),
            "matchTitle": config.match_title,
            "loginType": config.match_login_type,
            "smsAreaId": config.match_source_id,
            "matchKeyConfigured": bool(config.match_key),
            "platformTokenConfigured": bool(config.platform_token),
            "modelBaseUrl": config.model_base_url,
            "modelName": config.model_name,
            "modelApiKeyConfigured": bool(config.model_api_key),
            "modelApiKeyMasked": _mask_secret(config.model_api_key),
            "codexBaseUrl": config.codex_base_url,
            "codexModel": config.codex_model,
            "codexApiKeyConfigured": bool(config.codex_api_key),
            "codexApiKeyMasked": _mask_secret(config.codex_api_key),
            "codexMaxConcurrency": config.codex_max_concurrency,
            "codexSystemPrompt": config.codex_system_prompt or DEFAULT_CODEX_SYSTEM_PROMPT,
            "codexCtfSkillsEnabled": config.codex_ctf_skills_enabled,
            "codexAutoResumeInterrupted": config.codex_auto_resume_interrupted,
        }

    def _load_persisted(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(raw, Mapping):
            return
        try:
            current = self._config
            candidate = Config(
                match_url=_persisted_value(raw, "match_url", current.match_url),
                match_key=_persisted_value(raw, "match_key", current.match_key),
                platform_token=_persisted_value(
                    raw, "platform_token", current.platform_token
                ),
                match_title=_persisted_value(raw, "match_title", current.match_title),
                match_login_type=_persisted_int(
                    raw, "match_login_type", current.match_login_type
                ),
                match_source_id=_persisted_value(
                    raw, "match_source_id", current.match_source_id
                ),
                listen_addr=current.listen_addr,
                model_base_url=_persisted_value(
                    raw, "model_base_url", current.model_base_url
                ),
                model_api_key=_persisted_value(
                    raw, "model_api_key", current.model_api_key
                ),
                model_name=_persisted_value(raw, "model_name", current.model_name),
                codex_base_url=_persisted_value(
                    raw, "codex_base_url", current.codex_base_url
                ),
                codex_api_key=_persisted_value(
                    raw, "codex_api_key", current.codex_api_key
                ),
                codex_model=_persisted_value(raw, "codex_model", current.codex_model),
                codex_max_concurrency=_persisted_int(
                    raw, "codex_max_concurrency", current.codex_max_concurrency
                ),
                codex_system_prompt=_persisted_value(
                    raw, "codex_system_prompt", current.codex_system_prompt
                ),
                codex_ctf_skills_enabled=_persisted_bool(
                    raw, "codex_ctf_skills_enabled", current.codex_ctf_skills_enabled
                ),
                codex_auto_resume_interrupted=_persisted_bool(
                    raw,
                    "codex_auto_resume_interrupted",
                    current.codex_auto_resume_interrupted,
                ),
            )
            validate_runtime_config(candidate)
        except (TypeError, ValueError, ConfigError):
            return
        self._config = candidate

    def _persist(self, config: Config) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: getattr(config, key)
            for key in Config.__dataclass_fields__
            if key != "listen_addr"
        }
        temporary_name = ""
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self._path)
        except OSError as exc:
            raise ConfigError(f"保存运行时配置失败: {exc}") from exc
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)


def validate_runtime_config(config: Config) -> None:
    """Validate values shared by dotenv and the web configuration endpoint."""

    platform_values = (config.match_url, config.match_key, config.platform_token)
    if any(platform_values) and not all(platform_values):
        raise ConfigError(
            "match_url, match_key and platform_token must be configured together"
        )
    if config.match_url:
        try:
            parse_match_url(config.match_url)
        except MatchURLValidationError as exc:
            raise ConfigError(str(exc)) from exc
        _validate_opaque_value(config.match_key, "match_key", 512)
        _validate_opaque_value(config.platform_token, "platform_token", 4096)
        if len(config.match_title) > 512:
            raise ConfigError("match_title is invalid")
        if not 0 <= config.match_login_type <= 10:
            raise ConfigError("match_login_type is invalid")
    if not config.model_base_url and config.model_name:
        raise ConfigError("baseurl is required when model is set")
    if config.model_base_url and not config.model_name:
        raise ConfigError("model is required when baseurl is set")
    if config.model_base_url:
        _validate_http_url(config.model_base_url, "model baseurl")
    if not config.codex_base_url and config.codex_model:
        raise ConfigError("codexbaseurl is required when codexmodel is set")
    if config.codex_base_url and not config.codex_model:
        raise ConfigError("codexmodel is required when codexbaseurl is set")
    if config.codex_base_url:
        _validate_http_url(config.codex_base_url, "codex baseurl")
    if not 1 <= config.codex_max_concurrency <= 16:
        raise ConfigError("codexmaxconcurrency must be between 1 and 16")
    if len(config.codex_system_prompt) > 20_000:
        raise ConfigError("codexsystemprompt is invalid")


def _mask_secret(value: str) -> str:
    """Return a recognizable but non-reversible display form for a secret."""
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 8:
        return f"{value[:2]}…{value[-2:]}"
    return f"{value[:4]}…{value[-4:]}"


def _value(values: Mapping[str, Any], key: str, current: str) -> str:
    if key not in values or values[key] is None:
        return "" if key in values and values[key] is None else current
    if not isinstance(values[key], str):
        raise ConfigError(f"{key} must be a string")
    value = cast(str, values[key]).strip()
    return value.rstrip("/") if key.endswith("url") else value


def _persisted_value(values: Mapping[str, Any], key: str, current: str) -> str:
    value = values.get(key, current)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _persisted_int(values: Mapping[str, Any], key: str, current: int) -> int:
    value = values.get(key, current)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    return cast(int, value)


def _persisted_bool(values: Mapping[str, Any], key: str, current: bool) -> bool:
    value = values.get(key, current)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


def _int_value(values: Mapping[str, Any], key: str, current: int) -> int:
    if key not in values or values[key] is None:
        return current
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    return cast(int, value)


def _bool_value(values: Mapping[str, Any], key: str, current: bool) -> bool:
    if key not in values or values[key] is None:
        return current
    if not isinstance(values[key], bool):
        raise ConfigError(f"{key} must be a boolean")
    return cast(bool, values[key])


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"open {path}: {exc}") from exc
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ConfigError(f"invalid .env assignment on line {line_number}")
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            raise ConfigError(f"invalid .env assignment on line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _resolve(values: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    for key in keys:
        value = values.get(key, "").strip()
        if value:
            return value
    return ""


def _validate_http_url(value: str, label: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ConfigError(f"parse {label}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ConfigError(f"{label} must use http or https")
    if not parsed.hostname:
        raise ConfigError(f"{label} must include a host")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{label} must not include a query or fragment")


def _validate_opaque_value(value: str, label: str, maximum: int) -> None:
    if (
        not value
        or len(value) > maximum
        or any(ord(character) < 33 for character in value)
    ):
        raise ConfigError(f"{label} is invalid")


def _public_match_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    return f"https://match.ichunqiu.com{parsed.path.rstrip('/')}"


Load = load
