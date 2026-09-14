"""Prompt generation and bounded tool-calling AI solver."""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .agent import AnswerResult, ExerciseDetail, ExerciseGroup
from .download import (
    AttachmentInfo,
    ExerciseNotFound,
    Inspection,
    Result,
)
from .openai_client import Message, Tool, ToolCall, ToolFunction

MAX_MODEL_ROUNDS = 12


class SourceProtocol(Protocol):
    def exercises(self, refresh: bool = False) -> list[ExerciseGroup]: ...

    def exercise(self, exercise_id: int, refresh: bool = False) -> ExerciseDetail: ...

    def submit_flag(self, exercise_id: int, flag: str) -> AnswerResult: ...

    def build_environment(self, exercise_id: int) -> None: ...

    def recover_environment(self, exercise_id: int) -> None: ...


class AttachmentProtocol(Protocol):
    def attachments(self, exercise_id: int) -> list[AttachmentInfo]: ...

    def download(self, exercise_id: int, attachment_index: int) -> Result: ...

    def inspect(self, exercise_id: int, attachment_index: int) -> Inspection: ...


class ModelProtocol(Protocol):
    def complete(self, model: str, messages: list[Message], tools: list[Tool]) -> Message: ...

    def enabled(self) -> bool: ...


class ModelNotConfigured(RuntimeError):
    pass


class ModelRoundLimit(RuntimeError):
    pass


ErrModelNotConfigured = ModelNotConfigured
ErrModelRoundLimit = ModelRoundLimit


@dataclass(frozen=True)
class PromptResult:
    prompt: str
    model_enabled: bool
    model: str
    exercise_id: int
    category: str
    exercise_name: str
    attachments: tuple[AttachmentInfo, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "prompt": self.prompt,
            "modelEnabled": self.model_enabled,
            "exerciseId": self.exercise_id,
            "category": self.category,
            "exerciseName": self.exercise_name,
            "attachments": [item.to_dict() for item in self.attachments],
        }
        if self.model:
            result["model"] = self.model
        return result


@dataclass(frozen=True)
class RunResult:
    solved: bool = False
    flag: str = ""
    cleanup_attempted: bool = False
    cleanup_succeeded: bool = False
    output: str = ""
    writeup_path: str = ""
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "solved": self.solved,
            "cleanupAttempted": self.cleanup_attempted,
            "cleanupSucceeded": self.cleanup_succeeded,
            "output": self.output,
        }
        if self.flag:
            result["flag"] = self.flag
        if self.writeup_path:
            result["writeupPath"] = self.writeup_path
        if self.warning:
            result["warning"] = self.warning
        return result


@dataclass(frozen=True)
class _ExerciseData:
    category: str
    detail: ExerciseDetail
    attachments: tuple[AttachmentInfo, ...]


@dataclass
class _RunState:
    solved: bool = False
    flag: str = ""
    cleanup_attempted: bool = False
    cleanup_succeeded: bool = False


class SolverService:
    def __init__(
        self,
        source: SourceProtocol,
        attachments: AttachmentProtocol,
        model: ModelProtocol | None,
        model_name: str,
        writeup_root: str | Path = "writeups",
        workspace_root: str | Path | None = None,
    ) -> None:
        self.source = source
        self.attachments = attachments
        self.model = model
        self.model_name = model_name.strip()
        self.writeup_root = Path(writeup_root)
        self.workspace_root = Path(workspace_root) if workspace_root else detect_workspace_root()

    def configure_model(self, model: ModelProtocol | None, model_name: str) -> None:
        self.model = model
        self.model_name = model_name.strip()

    def enabled(self) -> bool:
        if self.model is None or not self.model_name:
            return False
        enabled = getattr(self.model, "enabled", None)
        return bool(enabled()) if callable(enabled) else True

    def prompt(self, exercise_id: int) -> PromptResult:
        data = self._load_exercise(exercise_id)
        return PromptResult(
            render_prompt(data, self.workspace_root),
            self.enabled(),
            self.model_name,
            data.detail.id,
            data.category,
            data.detail.name,
            data.attachments,
        )

    def prompt_pure(self, exercise_id: int) -> PromptResult:
        data = self._load_exercise(exercise_id)
        return PromptResult(
            render_pure_prompt(data, self.workspace_root),
            self.enabled(),
            self.model_name,
            data.detail.id,
            data.category,
            data.detail.name,
            data.attachments,
        )

    def run(self, exercise_id: int) -> RunResult:
        if not self.enabled():
            raise ModelNotConfigured("model is not configured")
        model = self.model
        if model is None:
            raise ModelNotConfigured("model is not configured")
        data = self._load_exercise(exercise_id)
        messages = [
            Message("system", "你是受限的 CTF 解题代理。只能处理用户指定题目，只能调用提供的竞赛工具。未获得平台正确响应时不得声称已解出；正确后必须输出完整中文 Markdown WP。"),
            Message("user", render_prompt(data, self.workspace_root)),
        ]
        tools = solver_tools()
        state = _RunState()
        for _round in range(MAX_MODEL_ROUNDS):
            try:
                message = model.complete(self.model_name, messages, tools)
            except Exception as exc:
                if state.solved:
                    warning = f"模型在正确提交后返回错误：{exc}"
                    return self._finish_solved(data, state, fallback_writeup(data, state, warning), warning)
                raise RuntimeError(f"model completion: {exc}") from exc
            if not message.role:
                message = Message("assistant", message.content, message.tool_call_id, message.tool_calls)
            messages.append(message)
            if not message.tool_calls:
                output = message.content.strip()
                if state.solved:
                    if not output:
                        output = fallback_writeup(data, state, "模型未返回 WP 内容")
                    return self._finish_solved(data, state, output, "")
                return RunResult(output=output)
            for call in message.tool_calls:
                content = self._execute_tool(exercise_id, call, state)
                messages.append(Message("tool", content, tool_call_id=call.id))
        if state.solved:
            warning = "model tool-call round limit reached"
            return self._finish_solved(data, state, fallback_writeup(data, state, warning), warning)
        raise ModelRoundLimit("model tool-call round limit reached")

    def _load_exercise(self, exercise_id: int) -> _ExerciseData:
        groups = self.source.exercises(False)
        category = ""
        found = False
        for group in groups:
            for summary in group.corpus:
                if summary.id == exercise_id:
                    category = group.name
                    found = True
                    break
            if found:
                break
        if not found:
            raise ExerciseNotFound("exercise not found")
        detail = self.source.exercise(exercise_id, False)
        attachments = tuple(self.attachments.attachments(exercise_id))
        return _ExerciseData(category, detail, attachments)

    def _execute_tool(self, exercise_id: int, call: ToolCall, state: _RunState) -> str:
        if call.type and call.type != "function":
            return tool_error("不支持的工具调用类型")
        name = call.function.name
        try:
            arguments = _decode_arguments(call.function.arguments)
        except ValueError:
            return tool_error(f"{name} 参数无效")
        if name == "refresh_exercise":
            if arguments:
                return tool_error("refresh_exercise 参数无效")
            try:
                return tool_success(self.source.exercise(exercise_id, True).to_dict())
            except Exception:
                return tool_error("题目状态刷新失败")
        if name == "download_attachment":
            index = _index_argument(arguments)
            if index is None:
                return tool_error("download_attachment 参数无效")
            try:
                return tool_success(self.attachments.download(exercise_id, index).to_dict())
            except Exception:
                return tool_error("附件下载失败")
        if name == "inspect_attachment":
            index = _index_argument(arguments)
            if index is None:
                return tool_error("inspect_attachment 参数无效")
            try:
                return tool_success(self.attachments.inspect(exercise_id, index).to_dict())
            except Exception:
                return tool_error("附件检查失败，请先下载附件")
        if name == "start_environment":
            if arguments:
                return tool_error("start_environment 参数无效")
            if state.solved:
                return tool_error("题目已解出，不允许重新启动环境")
            try:
                self.source.build_environment(exercise_id)
            except Exception:
                return tool_error("环境启动失败")
            return tool_success({"accepted": True})
        if name == "stop_environment":
            if arguments:
                return tool_error("stop_environment 参数无效")
            if state.solved and state.cleanup_attempted:
                return tool_success({"alreadyAttempted": True, "succeeded": state.cleanup_succeeded})
            try:
                self.source.recover_environment(exercise_id)
            except Exception:
                return tool_error("环境回收失败")
            return tool_success({"accepted": True})
        if name == "submit_flag":
            raw_flag = arguments.get("flag") if isinstance(arguments, dict) else None
            if not isinstance(raw_flag, str) or set(arguments) != {"flag"}:
                return tool_error("submit_flag 参数无效")
            try:
                flag = normalize_flag(raw_flag)
            except ValueError as exc:
                return tool_error(str(exc))
            if state.solved:
                return tool_success({"isCorrect": True, "normalizedFlag": state.flag, "alreadySolved": True})
            try:
                result: AnswerResult = self.source.submit_flag(exercise_id, flag)
            except Exception:
                return tool_error("Flag 提交失败")
            response: dict[str, Any] = {"isCorrect": result.is_correct, "normalizedFlag": flag}
            if not result.is_correct:
                return tool_success(response)
            state.solved = True
            state.flag = flag
            state.cleanup_attempted = True
            try:
                self.source.recover_environment(exercise_id)
                state.cleanup_succeeded = True
            except Exception:
                state.cleanup_succeeded = False
            response.update(
                {
                    "cleanupAttempted": True,
                    "cleanupSucceeded": state.cleanup_succeeded,
                    "nextAction": "立即输出完整中文 Markdown WP",
                }
            )
            return tool_success(response)
        return tool_error("不允许调用未知工具")

    def _finish_solved(self, data: _ExerciseData, state: _RunState, output: str, warning: str) -> RunResult:
        result = RunResult(True, state.flag, state.cleanup_attempted, state.cleanup_succeeded, output, warning=warning)
        try:
            path = self._save_writeup(data, output)
        except OSError:
            return RunResult(**{**result.__dict__, "warning": join_warning(warning, "WP 保存失败")})
        return RunResult(**{**result.__dict__, "writeup_path": path})

    def _save_writeup(self, data: _ExerciseData, content: str) -> str:
        root = self.writeup_root if str(self.writeup_root).strip() else Path("writeups")
        root_absolute = root.absolute()
        category = safe_writeup_segment(data.category, "Uncategorized")
        name = safe_writeup_segment(data.detail.name, "exercise")
        directory = root_absolute / category
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{data.detail.id}-{name}.md"
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.part-", dir=str(directory))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        display_root = root if not root.is_absolute() else Path(root.name)
        return (display_root / category / target.name).as_posix()


def render_prompt(data: _ExerciseData, workspace_root: Path) -> str:
    prompt = [
        "# CTF 题目自动解题任务\n\n",
        "你正在处理一项明确授权的 CTF 竞赛题目，只允许操作本题相关附件、靶机和答题接口。\n\n",
        "## 解题原则\n\n",
        "想一切办法解决题目：可以进行公开网络搜索，查阅公开资料、官方文档、技术文章和公开源码；对关键结论必须通过实际命令、脚本、请求或文件内容验证并记录证据。网络搜索只用于获取公开资料。\n\n",
        "## 安全边界\n\n",
        "禁止爆破、穷举、猜测、伪造或拼接 Flag；不能把未经验证的推测当成成功。禁止攻击 i春秋平台本身、登录接口、比赛接口、网关和其他竞赛基础设施；只允许访问本题明确授权的附件、靶机和本地 Web API。\n\n",
        render_exercise_block(data, workspace_root),
        render_tool_block(workspace_root),
        "\n## 强制工作流程\n\n",
        f"1. 先阅读 API 文档 `{workspace_root / 'docs' / 'api_doc.md'}`，确认题目详情、附件、启动环境、提交答案和回收环境的接口语义。\n",
        "2. 检查所有附件；尚未下载时先使用附件下载工具，再根据上面的本地文件路径分析。\n",
        "3. 解题过程中产生的题目文件、脚本和临时文件都保存到上面列出的本题工作目录，不要散落到项目其他目录；如确需额外工具，可从可信来源下载到本题工作目录后使用，禁止写入系统目录。\n",
        "4. 如果题目需要容器或靶机，先启动靶机，轮询题目状态直到连接信息可用；不要猜测地址或凭据。\n",
        "5. 完成分析与利用，得到 Flag 候选值后再提交。\n",
        "6. Flag 提交规范是硬性规则：只提交花括号以内的内容。示例：`flag{answer} -> answer`。不得提交前缀或花括号；其他前缀同样处理。\n",
        "7. 只有平台明确返回正确才算成功。成功后立即销毁靶机并确认清理结果。\n",
        "8. 成功后输出一份可复现的中文 Markdown WP，不得只给结论。\n\n",
        "## WP 输出结构\n\n# 题目名称与题号\n## 题解摘要\n## 解题分析\n## 附件与环境\n## 复现步骤\n## Flag 推导\n## 环境清理\n\n",
        "复现步骤必须包含实际使用的命令、脚本、请求或利用代码；Flag 推导要说明如何得到花括号内部值；环境清理要记录靶机销毁结果。\n",
    ]
    return "".join(prompt)


def render_pure_prompt(data: _ExerciseData, workspace_root: Path) -> str:
    return "".join(
        [
            "# CTF 题目纯解题任务\n\n",
            "你正在处理一项明确授权的 CTF 竞赛题目。这是纯解题模式：不启动任何容器或靶机、不阅读任何文档或配置文件、不调用竞赛 API 或提交 Flag，只根据题目说明和本地已下载附件推导答案。\n\n",
            "## 解题原则与安全边界\n\n",
            "可以搜索公开资料、官方文档和公开源码来补充背景知识，但必须以题目附件或可复现的本地分析为证据。禁止爆破、穷举、猜测、伪造或拼接 Flag；禁止攻击任何竞赛平台、登录接口、比赛接口或网关。\n\n",
            render_exercise_block(data, workspace_root),
            render_tool_block(workspace_root),
            "\n## 强制工作流程\n\n",
            "1. 只使用上面的题目说明和本地附件路径；禁止阅读任何文档或配置文件，包括 docs/api_doc.md 和 .env。\n",
            "2. 禁止启动、连接或回收任何容器/靶机，也禁止调用竞赛 API 或提交 Flag。\n",
            "3. 检查上面列出的本地附件；尚未下载的附件不可用，可以明确说明缺少该信息。\n",
            "4. 完成分析，推导 Flag 候选值。\n",
            "5. 最终输出必须先单独给出 Flag 值（花括号内部内容），再给出可复现的中文 Markdown WP。\n",
            "6. 无法从已有信息确定 Flag 时，说明原因和缺少的信息，不得编造。\n\n",
            "## WP 输出结构\n\n# 题目名称与题号\n## 题解摘要\n## 解题分析\n## 附件与环境（仅本地附件）\n## 复现步骤\n## Flag 推导\n## 环境清理\n\n",
            "复现步骤必须包含实际使用的命令、脚本或利用代码；Flag 推导要说明如何得到花括号内部值；环境清理说明本模式未启动任何容器。\n",
        ]
    )


def render_tool_block(workspace_root: Path) -> str:
    tools_root = workspace_root / "tools"
    manifest = tools_root / "installed-tools.txt"
    return (
        "\n## CTF 工具目录\n\n"
        f"本项目的可选工具安装根目录是 `{tools_root}`。\n"
        f"Python 工具虚拟环境是 `{tools_root / 'venv'}`，命令行工具目录是 `{tools_root / 'bin'}`。\n"
        f"工具清单文件是 `{manifest}`；如果文件存在，请先阅读它获取当前已安装工具、入口路径和基本用法。\n"
        f"内置字典目录是 `{tools_root / 'dictionaries'}`；需要授权的口令审计或候选值分析时可按需读取其中的文本文件，不要复制到系统目录，也不要把字典内容写入 WP。\n"
        f"本题工作目录通常是附件路径所在的目录（例如 `{workspace_root / 'download'}` 下对应题目目录）；新建脚本、解压文件和临时文件都放在该目录。\n"
        "使用工具前先检查对应路径和命令是否存在；不要把工具安装到系统目录，也不要修改与本题无关的文件。\n"
        "禁止运行上游技能包中的 `scripts/install_ctf_tools.sh`，也禁止执行会写入系统目录的 `apt`、`brew`、`gem`、`go install`、`cargo install` 等安装命令。缺少工具时，优先等待用户在本工作台工具管理中安装；确需临时使用时，只能将可信来源的便携归档或脚本下载、解压到本题工作目录。\n"
        "禁止直接调用 Docker CLI 或修改 Docker；题目环境只能通过本地 Web API 操作。\n\n"
    )


def render_exercise_block(data: _ExerciseData, workspace_root: Path) -> str:
    detail = data.detail
    description = detail.description.strip() or "（无题目说明）"
    lines = [
        "## 题目信息\n\n",
        f"- 题目类型：{data.category}\n",
        f"- 题目题号：{detail.id}\n",
        f"- 题目名称：{detail.name}\n",
        f"- 题目分值：{value_or_dash(detail.score)}\n",
        f"- 题目形态：{exercise_kind(detail, data.attachments)}\n",
        f"- 题目说明：\n{description}\n\n",
        "## 文件路径\n\n",
    ]
    if data.attachments:
        challenge_dir = workspace_root / Path(data.attachments[0].path).parent
    else:
        challenge_dir = (
            workspace_root
            / "download"
            / safe_writeup_segment(data.category, "Uncategorized")
            / f"{detail.id}-{safe_writeup_segment(detail.name, 'exercise')}"
        )
    lines.append(f"- 本题工作目录：{challenge_dir}\n")
    if not data.attachments:
        lines.append("- 本题没有附件。\n")
    else:
        for item in data.attachments:
            absolute = absolute_prompt_path(workspace_root, item.path)
            state = f"（已下载，{item.size} 字节）" if item.exists else "（尚未下载）"
            lines.append(f"- [{item.index}] {absolute}{state}\n")
    lines.append("所有文件路径均为绝对路径。\n")
    return "".join(lines)


def detect_workspace_root() -> Path:
    current = Path.cwd().absolute()
    for candidate in (current, *current.parents):
        if (candidate / "docs" / "api_doc.md").is_file():
            return candidate
    return current


def absolute_prompt_path(workspace_root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve(strict=False) if path.is_absolute() else (workspace_root / path).resolve(strict=False)


def exercise_kind(detail: ExerciseDetail, attachments: tuple[AttachmentInfo, ...] | list[AttachmentInfo]) -> str:
    has_attachment = bool(attachments) or bool(detail.attachment.files)
    has_container = detail.is_need_init or detail.is_need_check or bool(detail.endpoints) or bool(detail.endpoint_type)
    if has_attachment and has_container:
        return "混合题"
    if has_attachment:
        return "附件题"
    if has_container:
        return "容器题"
    return "信息题"


def value_or_dash(value: str) -> str:
    return value.strip() or "—"


def normalize_flag(raw: str) -> str:
    flag = raw.strip()
    open_index = flag.find("{")
    close_index = flag.rfind("}")
    if open_index >= 0 or close_index >= 0:
        if open_index < 0 or close_index < 0 or open_index >= close_index:
            raise ValueError("Flag 花括号格式无效")
        flag = flag[open_index + 1 : close_index].strip()
    if not flag:
        raise ValueError("Flag 内容不能为空")
    if len(flag) > 256:
        raise ValueError("Flag 内容不能超过 256 个字符")
    return flag


def solver_tools() -> list[Tool]:
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    index = {
        "type": "object",
        "properties": {"index": {"type": "integer", "minimum": 0}},
        "required": ["index"],
        "additionalProperties": False,
    }
    flag = {
        "type": "object",
        "properties": {"flag": {"type": "string", "minLength": 1, "maxLength": 512}},
        "required": ["flag"],
        "additionalProperties": False,
    }
    definitions = [
        ("refresh_exercise", "刷新当前题目详情并取得靶机状态、端点和凭据。", empty),
        ("download_attachment", "按附件索引将当前题目附件保存到可信本地路径。", index),
        ("inspect_attachment", "检查已下载附件的元数据、SHA-256 和有限内容预览。", index),
        ("start_environment", "启动当前题目的容器或靶机。", empty),
        ("stop_environment", "关闭当前题目的容器或靶机。", empty),
        ("submit_flag", "提交 Flag 候选；服务端会强制只提交最外层花括号内部值。", flag),
    ]
    return [Tool("function", ToolFunction(name, description, parameters, True)) for name, description, parameters in definitions]


def _decode_arguments(raw: str) -> dict[str, Any]:
    value = json.loads(raw.strip() or "{}")
    if not isinstance(value, dict):
        raise ValueError("arguments must be an object")
    return value


def _index_argument(arguments: Mapping[str, Any]) -> int | None:
    if set(arguments) != {"index"} or not isinstance(arguments.get("index"), int) or isinstance(arguments.get("index"), bool):
        return None
    index = int(arguments["index"])
    return index if index >= 0 else None


def tool_success(data: Any) -> str:
    return _tool_json({"ok": True, "data": _to_wire(data)})


def tool_error(message: str) -> str:
    return _tool_json({"ok": False, "error": message})


def _tool_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return '{"ok":false,"error":"工具结果编码失败"}'


def _to_wire(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _to_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_wire(item) for item in value]
    return value


def fallback_writeup(data: _ExerciseData, state: _RunState, reason: str) -> str:
    cleanup = "已自动回收" if state.cleanup_succeeded else "失败或未完成"
    return f"""# {data.detail.name} ({data.detail.id})

## 题解摘要

平台已确认 Flag 正确，但模型未能返回完整 WP：{reason}。

## 解题分析

模型响应中断，详细分析未能保存。

## 复现步骤

请结合模型会话与题目附件补充复现步骤。

## Flag 推导

提交的花括号内部值：{state.flag}

## 环境清理

{cleanup}。
"""


def safe_writeup_segment(raw: str, fallback: str) -> str:
    chars: list[str] = []
    for char in raw.strip():
        if unicodedata.category(char) == "Cc" or char in '<>:"/\\|?*':
            chars.append("_")
        else:
            chars.append(char)
    value = "".join(chars).strip(" .")
    while ".." in value:
        value = value.replace("..", "_")
    return truncate_string(value or fallback, 160)


def truncate_string(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def join_warning(existing: str, addition: str) -> str:
    return addition if not existing else f"{existing}；{addition}"


Service = SolverService
New = SolverService
