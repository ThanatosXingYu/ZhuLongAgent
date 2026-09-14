from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, Request

import pytest
from fastapi.testclient import TestClient

import internal.download as download_module
from internal.attachment_tasks import AttachmentTaskManager
from internal.agent import (
    APIError,
    AnswerResult,
    Attachment,
    ExerciseDetail,
    ExerciseGroup,
    ExerciseSummary,
    File,
    Overview,
    PlatformNotConfigured,
)
from internal.cached import CachedService
from internal.codex import (
    CodexManager,
    CodexError,
    Event,
    ManagerConfig,
    ProcessConfig,
    ProcessResult,
    _launch_system_terminal,
    _prepare_codex_home,
    _process_environment,
    build_command,
    build_interactive_command,
)
from internal.codex_skills import CTF_SKILL_NAMES, prepare_codex_skills
from internal.config import (
    DEFAULT_CODEX_MAX_CONCURRENCY,
    Config,
    RuntimeConfigStore,
    load,
)
from internal.download import (
    AttachmentInfo,
    DownloadFailed,
    DownloadService,
    TooLarge,
)
from internal.openai_client import FunctionCall, Message, ToolCall
from internal.solver import ModelNotConfigured, SolverService, normalize_flag
from internal.tool_manager import TOOL_SPECS, ToolBusyError, ToolManager
from internal.web import create_app
from main import _listen_parts


def test_frontend_waits_for_platform_configuration_before_loading_workspace() -> None:
    script = (Path(__file__).parents[2] / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "const config = await loadRuntimeConfig();" in script
    assert "if (config && config.configured && state.authenticated)" in script
    assert "Promise.allSettled([loadRuntimeConfig(), loadOverview()" not in script
    assert "els.settingMatchUrl.value = matchUrl;" in script
    assert "refreshWorkspaceAfterLogin({ notify: true })" in script
    assert "renderRichText(detail.description" in script
    assert "renderRichText(detail.content" in script
    assert "innerHTML" not in script
    assert "const SCORE_REFRESH_MS = 5000;" in script
    assert "const NOTICE_REFRESH_MS = 10000;" in script
    assert "const ATTACHMENT_TASK_POLL_MS = 500;" in script
    assert 'scope: "single"' in script
    assert "controlAttachmentDownload" in script
    assert 'createElement("span", "meta-chip info", exerciseCategory(detail))' in script
    assert "混合题" not in script
    assert "persistCollapsedGroups" in script
    assert 'second: "2-digit"' in script

    page = (Path(__file__).parents[2] / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "将左侧图片旋转到自然竖直方向" in page
    assert 'id="platform-team-info"' in page
    assert 'class="topbar-overview"' in page
    assert "每 10 秒自动刷新公告" in page
    assert 'id="attachment-progress-bar"' in page
    assert 'id="pause-attachment-download"' in page
    assert 'id="resume-attachment-download"' in page
    assert 'id="cancel-attachment-download"' in page
    assert 'id="probe-attachment-sizes"' not in page
    assert 'id="tool-manager-list"' in page
    assert "Codex CLI 任务" in page
    assert 'id="model-options"' in page and 'class="model-options-menu"' in page
    assert 'id="codex-model-options"' in page and 'role="listbox"' in page
    assert 'id="open-codex-folder"' in page
    assert 'id="copy-codex-resume"' in page
    assert 'id="codex-task-resume"' not in page
    assert 'id="codex-task-writeup"' not in page
    assert "<datalist" not in page


class Source:
    def __init__(self, url: str = "") -> None:
        self.groups = [ExerciseGroup(1, "Web", 1, (ExerciseSummary(7, "shopping"),))]
        self.detail = ExerciseDetail(
            7,
            "shopping",
            "分析附件",
            score="100",
            attachment=Attachment((File("shopping.zip", url, "zip"),)),
        )
        self.refreshes: list[bool] = []
        self.submitted: list[str] = []
        self.recovered = 0

    def exercises(self, refresh: bool = False) -> list[ExerciseGroup]:
        self.refreshes.append(refresh)
        return self.groups

    def overview(self, _refresh: bool = False) -> Overview:
        return Overview(1.0, 1)

    def exercise(self, _id: int, refresh: bool = False) -> ExerciseDetail:
        self.refreshes.append(refresh)
        return self.detail

    def submit_flag(self, _id: int, flag: str) -> AnswerResult:
        self.submitted.append(flag)
        return AnswerResult(True)

    def build_environment(self, _id: int) -> None:
        return None

    def recover_environment(self, _id: int) -> None:
        self.recovered += 1


def test_platform_settings_are_not_loaded_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "match_url=https://match.ichunqiu.com/file\n"
        "platform_token=file\n"
        "baseurl=https://model.example.test/v1\n"
        "model=ignored-model\n"
        "codexmaxconcurrency=99\n"
        "listenaddr=127.0.0.1:1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATCH_URL", "https://match.ichunqiu.com/override")
    monkeypatch.setenv("BASEURL", "https://override-model.example.test/v1")
    config = load(env)
    assert config.match_url == ""
    assert config.match_key == ""
    assert config.platform_token == ""
    assert config.model_base_url == ""
    assert config.model_name == ""
    assert config.codex_max_concurrency == DEFAULT_CODEX_MAX_CONCURRENCY == 5
    assert config.listen_addr == "127.0.0.1:1"


def test_config_can_start_empty_and_runtime_store_masks_secrets(tmp_path: Path) -> None:
    assert load(tmp_path / "missing.env") == Config()
    store = RuntimeConfigStore(Config(), tmp_path / ".runtime-config.json")
    updated = store.update(
        {
            "match_url": "https://match.ichunqiu.com/demo-match",
            "match_key": "demo-match-key",
            "platform_token": "login:demo:not-a-real-token",
            "match_title": "演示比赛",
            "match_login_type": 4,
            "model_base_url": "https://model.example.test/v1/",
            "model_name": "solver",
            "model_api_key": "model-secret",
        }
    )
    assert updated.match_url == "https://match.ichunqiu.com/demo-match"
    public = store.public()
    assert public["configured"] is True
    assert public["matchTitle"] == "演示比赛"
    assert public["loginType"] == 4
    assert public["matchKeyConfigured"] is True
    assert public["platformTokenConfigured"] is True
    assert public["modelApiKeyConfigured"] is True
    assert "demo-match-key" not in json.dumps(public)
    assert "login:demo:not-a-real-token" not in json.dumps(public)
    persisted = json.loads(
        (tmp_path / ".runtime-config.json").read_text(encoding="utf-8")
    )
    assert persisted["match_key"] == "demo-match-key"
    assert persisted["platform_token"] == "login:demo:not-a-real-token"


def test_runtime_store_persists_ctf_skill_toggle(tmp_path: Path) -> None:
    path = tmp_path / ".runtime-config.json"
    store = RuntimeConfigStore(Config(), path)
    assert store.get().codex_ctf_skills_enabled is True
    updated = store.update({"codex_ctf_skills_enabled": False})
    assert updated.codex_ctf_skills_enabled is False
    assert store.public()["codexCtfSkillsEnabled"] is False
    reloaded = RuntimeConfigStore(Config(), path)
    assert reloaded.get().codex_ctf_skills_enabled is False


def test_runtime_config_web_endpoint_updates_ctf_skill_toggle(tmp_path: Path) -> None:
    store = RuntimeConfigStore(Config(), tmp_path / ".runtime-config.json")
    app = create_app(Source(), config_store=store)
    with TestClient(app) as client:
        response = client.put("/api/config", json={"codexCtfSkillsEnabled": False})
        assert response.status_code == 200
        assert response.json()["data"]["codexCtfSkillsEnabled"] is False


def test_codex_skills_are_workspace_local_and_removable(tmp_path: Path) -> None:
    source_root = tmp_path / "tools" / "skills" / "ctf-skills"
    for name in CTF_SKILL_NAMES:
        skill = source_root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    codex_home = tmp_path / ".codex-home"
    prepare_codex_skills(tmp_path, codex_home, enabled=True)
    assert all((codex_home / "skills" / name / "SKILL.md").is_file() for name in CTF_SKILL_NAMES)
    (codex_home / "skills" / "unmanaged").mkdir()
    prepare_codex_skills(tmp_path, codex_home, enabled=False)
    assert not any((codex_home / "skills" / name).exists() for name in CTF_SKILL_NAMES)
    assert (codex_home / "skills" / "unmanaged").is_dir()


def test_runtime_store_accepts_resolved_key_different_from_match_entry_key(
    tmp_path: Path,
) -> None:
    store = RuntimeConfigStore(Config(), tmp_path / ".runtime-config.json")
    incoming_entry_key = "input-entry-key"
    resolved_match_key = "resolved-match-key"

    updated = store.update(
        {
            "match_url": f"https://match.ichunqiu.com/index?k={incoming_entry_key}",
            "match_key": resolved_match_key,
            "platform_token": "anonymous:not-a-real-token",
        }
    )

    assert updated.match_key == resolved_match_key
    public = store.public()
    assert public["matchUrl"] == "https://match.ichunqiu.com/index"
    assert incoming_entry_key not in json.dumps(public)
    assert resolved_match_key not in json.dumps(public)


def test_runtime_store_ignores_unknown_platform_fields(tmp_path: Path) -> None:
    path = tmp_path / ".runtime-config.json"
    path.write_text(
        json.dumps(
            {
                "platform_host": "https://unused-platform.example.test",
                "platform_secret": "unused-secret",
                "model_base_url": "https://model.example.test/v1",
                "model_name": "solver",
            }
        ),
        encoding="utf-8",
    )
    loaded = RuntimeConfigStore(Config(), path).get()
    assert loaded.match_url == ""
    assert loaded.match_key == ""
    assert loaded.platform_token == ""
    assert loaded.model_name == "solver"


def test_runtime_config_web_endpoint_only_updates_model_settings(
    tmp_path: Path,
) -> None:
    store = RuntimeConfigStore(Config(), tmp_path / ".runtime-config.json")
    app = create_app(Source(), config_store=store)
    with TestClient(app) as client:
        response = client.get("/api/config")
        assert (
            response.status_code == 200
            and response.json()["data"]["configured"] is False
        )
        response = client.put(
            "/api/config",
            json={
                "modelBaseUrl": "https://model.example.test/v1",
                "modelName": "solver",
            },
        )
        assert response.status_code == 200
        assert response.json()["data"]["modelName"] == "solver"
        response = client.put(
            "/api/config",
            json={
                "matchUrl": "https://match.ichunqiu.com/demo",
                "platformToken": "unused",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_CONFIG"


def test_model_catalog_endpoint_returns_provider_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RuntimeConfigStore(Config(), tmp_path / ".runtime-config.json")
    monkeypatch.setattr("internal.web.fetch_models", lambda base_url, api_key: ["alpha", "beta"])
    app = create_app(Source(), config_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/models",
            json={"baseUrl": "https://model.example.test/v1", "apiKey": "secret"},
        )
    assert response.status_code == 200
    assert response.json()["data"] == {"models": ["alpha", "beta"], "kind": "model"}


def test_listen_address_parsing_matches_go_tcp_address_forms() -> None:
    assert _listen_parts("127.0.0.1:8080") == ("127.0.0.1", 8080)
    assert _listen_parts(":8080") == ("0.0.0.0", 8080)
    assert _listen_parts("[::1]:8080") == ("::1", 8080)
    with pytest.raises(ValueError, match="host:port"):
        _listen_parts("8080")
    with pytest.raises(ValueError, match="host:port"):
        _listen_parts("::1:8080")


def test_cache_refresh_retries_rate_limit_and_preserves_value() -> None:
    class SourceStub:
        def __init__(self) -> None:
            self.calls = 0

        def overview(self, _refresh: bool = False) -> Overview:
            self.calls += 1
            if self.calls < 3:
                raise APIError("40001", "busy", 200)
            return Overview(1.0, self.calls)

    waits: list[float] = []
    source = SourceStub()
    cached = CachedService(source, waits.append)
    assert cached.overview().stage_rank == 3
    assert waits == [0.25, 0.5]
    assert cached.overview().stage_rank == 3
    assert source.calls == 3


def test_cache_retries_rate_limit_errors_wrapped_by_a_service() -> None:
    class SourceStub:
        def __init__(self) -> None:
            self.calls = 0

        def overview(self, _refresh: bool = False) -> Overview:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("service wrapper") from APIError("429", "busy", 429)
            return Overview(2.0, self.calls)

    waits: list[float] = []
    source = SourceStub()
    cached = CachedService(source, waits.append)
    assert cached.overview().stage_rank == 2
    assert source.calls == 2
    assert waits == [0.25]


def test_download_sanitizes_paths_and_inspects_content(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = Source(f"http://127.0.0.1:{server.server_port}/file")
        source.groups = [
            ExerciseGroup(1, "../Web\\Root", 1, (ExerciseSummary(7, "../../shop"),))
        ]
        source.detail = ExerciseDetail(
            7,
            "../../shop",
            attachment=Attachment(
                (
                    File(
                        "../../a:*?",
                        f"http://127.0.0.1:{server.server_port}/file",
                        "txt",
                    ),
                )
            ),
        )
        service = DownloadService(tmp_path / "download", source)
        result = service.download(7, 0)
        assert ".." not in result.path
        inspection = service.inspect(7, 0)
        assert inspection.preview == "hello"
        assert inspection.encoding == "utf-8"
        assert inspection.content_type.startswith("text/plain")
    finally:
        server.shutdown()
        thread.join()


def test_download_reloads_system_proxy_for_each_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy_states = iter(
        (
            {"https": "http://127.0.0.1:18080"},
            {},
        )
    )
    proxy_handlers: list[dict[str, str]] = []

    class Opener:
        def open(self, request: Request, *, timeout: float) -> object:
            assert request.full_url == "https://files.example.test/a.zip"
            assert timeout == 300.0
            return object()

    def build(*handlers: object) -> Opener:
        proxy = next(
            handler for handler in handlers if isinstance(handler, ProxyHandler)
        )
        proxy_handlers.append(dict(proxy.proxies))
        return Opener()

    monkeypatch.setattr(download_module, "getproxies", lambda: next(proxy_states))
    monkeypatch.setattr(download_module, "build_opener", build)
    service = DownloadService(tmp_path / "download")

    service._open("https://files.example.test/a.zip")
    service._open("https://files.example.test/a.zip")

    assert proxy_handlers == [{"https": "http://127.0.0.1:18080"}, {}]


def test_attachment_size_probe_and_background_download_progress(
    tmp_path: Path,
) -> None:
    payload = b"attachment-data" * 8192

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = Source(f"http://127.0.0.1:{server.server_port}/attachment")
        service = DownloadService(tmp_path / "download", source)
        tasks = AttachmentTaskManager(service, probe_workers=2)

        probe = tasks.start_probe()
        for _ in range(100):
            probe = tasks.probe_status()
            if probe["status"] != "running":
                break
            time.sleep(0.01)
        assert probe["status"] == "completed"
        assert probe["resolvedFiles"] == 1
        attachment = service.catalog().exercises[0].attachments[0]
        assert attachment.size == len(payload)

        task = tasks.start_all()
        task_id = str(task["id"])
        for _ in range(100):
            task = tasks.get(task_id)
            if task["status"] != "running":
                break
            time.sleep(0.01)
        assert task["status"] == "completed"
        assert task["totalFiles"] == 1
        assert task["completedFiles"] == 1
        assert task["downloadedFiles"] == 1
        assert task["totalBytes"] == len(payload)
        assert task["transferredBytes"] == len(payload)
        speed = task["speedBytesPerSecond"]
        assert speed > 0
        time.sleep(0.02)
        assert tasks.get(task_id)["speedBytesPerSecond"] == speed
    finally:
        server.shutdown()
        thread.join()


def test_attachment_download_can_pause_resume_and_cancel(tmp_path: Path) -> None:
    payload = b"controlled-attachment" * (4 * 1024 * 1024 // 21)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                for offset in range(0, len(payload), 16 * 1024):
                    self.wfile.write(payload[offset : offset + 16 * 1024])
                    self.wfile.flush()
                    time.sleep(0.001)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, _format: str, *args: object) -> None:
            return

    def wait_until(
        tasks: AttachmentTaskManager,
        task_id: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any]:
        snapshot = tasks.get(task_id)
        for _ in range(400):
            if predicate(snapshot):
                return snapshot
            time.sleep(0.005)
            snapshot = tasks.get(task_id)
        return snapshot

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = Source(f"http://127.0.0.1:{server.server_port}/attachment")
        service = DownloadService(tmp_path / "download", source)
        tasks = AttachmentTaskManager(service)

        task_id = str(tasks.start_all()["id"])
        active = wait_until(
            tasks, task_id, lambda item: item["currentBytes"] >= 1024 * 1024
        )
        assert active["status"] == "running"
        assert tasks.pause(task_id)["status"] == "paused"
        time.sleep(0.08)
        paused_bytes = tasks.get(task_id)["transferredBytes"]
        time.sleep(0.08)
        assert tasks.get(task_id)["transferredBytes"] == paused_bytes
        assert tasks.resume(task_id)["status"] == "running"
        completed = wait_until(
            tasks, task_id, lambda item: item["status"] != "running"
        )
        assert completed["status"] == "completed"
        target = tmp_path / "download" / "Web" / "7-shopping" / "shopping.zip"
        assert target.read_bytes() == payload

        service.clear_exercise(7)
        task_id = str(tasks.start_all()["id"])
        active = wait_until(
            tasks, task_id, lambda item: item["currentBytes"] >= 1024 * 1024
        )
        assert active["status"] == "running"
        assert tasks.cancel(task_id)["status"] in {"cancelling", "cancelled"}
        cancelled = wait_until(
            tasks, task_id, lambda item: item["status"] == "cancelled"
        )
        assert cancelled["status"] == "cancelled"
        assert not target.exists()
        assert not list((tmp_path / "download").rglob("*.part-*"))
    finally:
        server.shutdown()
        thread.join()


def test_attachment_category_download_uses_display_category() -> None:
    class Tasks:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def start_all(self) -> dict[str, Any]:
            self.calls.append(("all", ""))
            return {"scope": "all"}

        def start_category(self, category: str) -> dict[str, Any]:
            self.calls.append(("category", category))
            return {"scope": "category", "category": category}

        def start_exercise(self, exercise_id: int) -> dict[str, Any]:
            self.calls.append(("exercise", str(exercise_id)))
            return {"scope": "exercise"}

        def start_single(self, exercise_id: int, index: int) -> dict[str, Any]:
            self.calls.append(("single", f"{exercise_id}:{index}"))
            return {"scope": "single"}

        def start_redownload(self, exercise_id: int) -> dict[str, Any]:
            self.calls.append(("redownload", str(exercise_id)))
            return {"scope": "redownload"}

    tasks = Tasks()
    app = create_app(Source(), attachment_tasks=tasks)
    trusted = {"X-GCSIS-Action": "attachment-manager"}
    with TestClient(app) as client:
        response = client.post(
            "/api/attachments/downloads",
            headers=trusted,
            json={"scope": "category", "category": "Web"},
        )
        assert response.status_code == 202
        assert tasks.calls == [("category", "Web")]
        response = client.post(
            "/api/attachments/downloads", headers=trusted, json={"scope": "category"}
        )
        assert response.status_code == 400
        response = client.post(
            "/api/attachments/downloads",
            headers=trusted,
            json={"scope": "category", "category": "Web", "exerciseId": 7},
        )
        assert response.status_code == 400


def test_tool_manager_api_requires_action_header_and_forwards_selection() -> None:
    class Tools:
        def __init__(self) -> None:
            self.selection: tuple[list[str], bool] | None = None

        def catalog(self) -> dict[str, Any]:
            return {
                "tools": [{"name": "sqlmap", "installed": False}],
                "status": "idle",
            }

        def install(self, names: list[str] | None = None, all_tools: bool = False) -> dict[str, Any]:
            self.selection = (names or [], all_tools)
            return {"status": "running"}

    tools = Tools()
    app = create_app(Source(), tools=tools)
    trusted = {"X-GCSIS-Action": "tools-manager"}
    with TestClient(app) as client:
        response = client.get("/api/tools")
        assert response.status_code == 200
        assert response.json()["data"]["tools"][0]["name"] == "sqlmap"
        response = client.post("/api/tools", json={"toolIds": ["sqlmap"]})
        assert response.status_code == 403
        response = client.post("/api/tools", headers=trusted, json={})
        assert response.status_code == 400
        response = client.post(
            "/api/tools", headers=trusted, json={"toolIds": ["sqlmap"]}
        )
        assert response.status_code == 202
        assert tools.selection == (["sqlmap"], False)
        response = client.post(
            "/api/tools", headers=trusted, json={"toolIds": [], "all": True}
        )
        assert response.status_code == 202
        assert tools.selection == ([], True)


def test_tool_manager_cancel_endpoint_requires_header_and_forwards_request() -> None:
    class Tools:
        def __init__(self) -> None:
            self.cancelled = False

        def catalog(self) -> dict[str, Any]:
            return {"tools": [], "status": "running"}

        def install(
            self, names: list[str] | None = None, all_tools: bool = False
        ) -> dict[str, Any]:
            del names, all_tools
            return {"status": "running"}

        def cancel(self) -> dict[str, Any]:
            self.cancelled = True
            return {"status": "cancelling"}

    tools = Tools()
    app = create_app(Source(), tools=tools)
    trusted = {"X-GCSIS-Action": "tools-manager"}
    with TestClient(app) as client:
        response = client.post("/api/tools/cancel")
        assert response.status_code == 403
        response = client.post("/api/tools/cancel", headers=trusted)
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "cancelling"
        assert tools.cancelled is True


def test_tool_manager_uninstall_is_scoped_and_removes_python_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ToolManager(tmp_path)
    launcher = manager.root / "bin" / "sqlmap"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    pip = manager.venv / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    pip.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        manager,
        "_run_command",
        lambda command, timeout: calls.append(command),
    )

    result = manager.uninstall("sqlmap")

    assert result["status"] == "idle"
    assert not launcher.exists()
    assert calls == [[str(pip), "uninstall", "-y", "sqlmap"]]
    # Uninstall is restricted to the workspace tools root and leaves the
    # application's own virtual environment untouched.
    app_venv_marker = tmp_path / ".venv" / "marker"
    app_venv_marker.parent.mkdir()
    app_venv_marker.write_text("keep", encoding="utf-8")
    assert app_venv_marker.read_text(encoding="utf-8") == "keep"


def test_tool_manager_uninstall_removes_binary_payloads(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    exiftool_root = manager.root / "exiftool"
    exiftool_root.mkdir(parents=True)
    (exiftool_root / "exiftool").write_text("binary", encoding="utf-8")
    launcher = manager.root / "bin" / "exiftool"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    (manager.root / "exiftool.tar.gz").write_bytes(b"archive")

    manager.uninstall("exiftool")

    assert not exiftool_root.exists()
    assert not launcher.exists()
    assert not (manager.root / "exiftool.tar.gz").exists()
    assert manager.root.exists()


def test_basic_library_bundle_has_no_duplicate_cards() -> None:
    bundle = next(spec for spec in TOOL_SPECS if spec.name == "basic-libraries")
    assert set(bundle.packages) == {"requests", "gmpy2", "pycryptodome"}
    assert not {"requests", "gmpy2", "pycryptodome"}.intersection(
        spec.name for spec in TOOL_SPECS if spec.name != bundle.name
    )


def test_checksec_uninstall_does_not_remove_pwntools_command(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    standalone = manager.root / "bin" / "checksec"
    bundled = manager.venv / "bin" / "checksec"
    standalone.parent.mkdir(parents=True)
    bundled.parent.mkdir(parents=True)
    standalone.write_text("#!/bin/sh\n", encoding="utf-8")
    bundled.write_text("#!/bin/sh\n", encoding="utf-8")

    manager.uninstall("checksec")

    assert not standalone.exists()
    assert bundled.exists()


def test_checksec_uninstall_cleans_legacy_workspace_files(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    manager.root.mkdir(parents=True, exist_ok=True)
    for name in ("checksec", "checksec.sh", ".checksec.part"):
        (manager.root / name).write_text("legacy", encoding="utf-8")

    manager.uninstall("checksec")

    assert all(not (manager.root / name).exists() for name in ("checksec", "checksec.sh", ".checksec.part"))


def test_checksec_uninstall_removes_owned_symlink_without_touching_target(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    target = tmp_path / "external-checksec"
    target.write_text("keep", encoding="utf-8")
    launcher = manager.root / "bin" / "checksec"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(target)

    manager.uninstall("checksec")

    assert not launcher.exists()
    assert target.read_text(encoding="utf-8") == "keep"


def test_tool_manager_detects_windows_site_packages_layout(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    site_packages = manager.venv / "Lib" / "site-packages"
    (site_packages / "demo_pkg-1.0.dist-info").mkdir(parents=True)
    assert manager._venv_site_packages() == site_packages


def test_impacket_uses_the_console_script_published_by_pypi(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    spec = next(spec for spec in TOOL_SPECS if spec.name == "impacket")
    assert spec.command == "smbclient.py"
    assert manager._venv_command(spec.command).name == "smbclient.py"


def test_source_tools_report_source_only_execution_mode(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    source_tool = next(row for row in manager.catalog()["tools"] if row["name"] == "7z")
    assert source_tool["execution"] == "source-only"
    assert "不自动编译" in source_tool["supportNote"]


def test_ropper_is_reported_unavailable_on_python_314_or_newer(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    row = next(item for item in manager.catalog()["tools"] if item["name"] == "ropper")
    if __import__("sys").version_info >= (3, 14):
        assert row["supported"] is False
        assert "Python 3.14" in row["supportNote"]


def test_tool_manager_download_cleans_partial_archive_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ToolManager(tmp_path)
    target = manager.root / "failed-download.zip"
    manager.root.mkdir(parents=True, exist_ok=True)

    class FailingResponse:
        headers: dict[str, str] = {}

        def __enter__(self) -> "FailingResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _size: int) -> bytes:
            raise URLError("upstream unavailable")

    monkeypatch.setattr(
        "internal.tool_manager.urllib.request.urlopen",
        lambda *_args, **_kwargs: FailingResponse(),
    )

    with pytest.raises(URLError):
        manager._download("https://example.invalid/tool.zip", target)

    assert not target.exists()


def test_tool_manager_does_not_reinstall_existing_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ToolManager(tmp_path)
    monkeypatch.setattr(manager, "_installed", lambda spec: spec.name == "sqlmap")

    result = manager.install(["sqlmap"])

    assert result["status"] == "idle"
    assert manager._status == "idle"


def test_tool_manager_uninstall_rejects_unknown_or_busy_tools(tmp_path: Path) -> None:
    manager = ToolManager(tmp_path)
    with pytest.raises(ValueError, match="未知工具"):
        manager.uninstall("not-a-tool")
    manager._status = "running"
    with pytest.raises(ToolBusyError, match="安装任务正在运行"):
        manager.uninstall("sqlmap")


def test_tool_manager_uninstall_api_requires_header_and_supports_delete() -> None:
    class Tools:
        def __init__(self) -> None:
            self.uninstalled: list[str] = []

        def catalog(self) -> dict[str, Any]:
            return {"tools": [], "status": "idle"}

        def install(
            self, names: list[str] | None = None, all_tools: bool = False
        ) -> dict[str, Any]:
            del names, all_tools
            return {"status": "running"}

        def cancel(self) -> dict[str, Any]:
            return {"status": "idle"}

        def uninstall(self, name: str) -> dict[str, Any]:
            self.uninstalled.append(name)
            return {"status": "idle", "removed": name}

    tools = Tools()
    app = create_app(Source(), tools=tools)
    trusted = {"X-GCSIS-Action": "tools-manager"}
    with TestClient(app) as client:
        response = client.delete("/api/tools/sqlmap")
        assert response.status_code == 403
        response = client.delete("/api/tools/sqlmap", headers=trusted)
        assert response.status_code == 200
        assert response.json()["data"]["removed"] == "sqlmap"
        response = client.post("/api/tools/exiftool/uninstall", headers=trusted)
        assert response.status_code == 200
        assert tools.uninstalled == ["sqlmap", "exiftool"]
        response = client.post("/api/tools/cancel", headers=trusted)
        assert response.status_code == 200


def test_tool_manager_keeps_tools_and_caches_under_visible_workspace_root(
    tmp_path: Path,
) -> None:
    manager = ToolManager(tmp_path)
    assert manager.root == tmp_path / "tools"
    assert manager.catalog()["root"] == "tools"
    environment = manager._local_environment()
    assert environment["PIP_CACHE_DIR"] == str(tmp_path / "tools" / "cache" / "pip")
    assert environment["XDG_CACHE_HOME"] == str(tmp_path / "tools" / "cache")
    assert environment["TMPDIR"] == str(tmp_path / "tools" / "tmp")
    assert not (tmp_path / ".tools").exists()


def test_tool_catalog_exposes_install_categories_and_idle_progress(
    tmp_path: Path,
) -> None:
    manager = ToolManager(tmp_path)
    catalog = manager.catalog()
    categories = {item["category"] for item in catalog["tools"]}
    assert categories == {"python-library", "go-project", "binary-package"}
    # Built-in entries are always available and count as installed;
    # third-party packages and external binaries remain uninstalled.
    builtin_names = {item["name"] for item in catalog["tools"] if item["kind"] == "builtin"}
    assert {"json", "base64", "hashlib", "re", "struct", "urllib"} <= builtin_names
    assert catalog["installedCount"] == len(builtin_names)
    assert catalog["toolCount"] == len(catalog["tools"])
    assert catalog["progress"]["elapsed"] == 0.0
    assert catalog["progress"]["indeterminate"] is True
    exiftool = next(item for item in catalog["tools"] if item["name"] == "exiftool")
    assert exiftool["installUrl"] == "https://github.com/exiftool/exiftool"
    json_tool = next(item for item in catalog["tools"] if item["name"] == "json")
    assert json_tool["installed"] is True
    assert json_tool["kind"] == "builtin"
    assert json_tool["subgroup"] == "基础库"


def test_tool_manager_writes_workspace_manifest_with_paths_and_usage(
    tmp_path: Path,
) -> None:
    manager = ToolManager(tmp_path)
    launcher = manager.root / "bin" / "ffuf"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    manager._write_manifest()

    manifest = manager.root / "installed-tools.txt"
    content = manifest.read_text(encoding="utf-8")
    assert manager.catalog()["manifest"] == "tools/installed-tools.txt"
    assert "ffuf" in content
    assert str(launcher) in content
    assert "基本用法" in content


def test_tool_manager_python_launcher_keeps_tool_caches_in_workspace(
    tmp_path: Path,
) -> None:
    manager = ToolManager(tmp_path)
    manager._write_venv_launcher("pwn")
    launcher = (tmp_path / "tools" / "bin" / "pwn").read_text(encoding="utf-8")
    assert f"XDG_CACHE_HOME={tmp_path / 'tools' / 'cache'}" in launcher
    assert f"XDG_CONFIG_HOME={tmp_path / 'tools' / 'cache' / 'config'}" in launcher
    assert f"TMPDIR={tmp_path / 'tools' / 'tmp'}" in launcher


def test_codex_delete_remains_available_when_cli_is_not_configured() -> None:
    class Codex:
        def __init__(self) -> None:
            self.deleted = ""

        def enabled(self) -> bool:
            return False

        def delete(self, task_id: str) -> None:
            self.deleted = task_id

    codex = Codex()
    app = create_app(Source(), codex=codex)
    with TestClient(app) as client:
        response = client.delete("/api/codex/tasks/task-1")
        assert response.status_code == 200
        assert response.json()["data"] == {"deleted": True}
        assert codex.deleted == "task-1"


def test_download_rejects_oversized_response(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"12345"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = Source(f"http://127.0.0.1:{server.server_port}/file")
        with pytest.raises(TooLarge):
            DownloadService(tmp_path / "download", source, max_bytes=4).download(7, 0)
        assert not list((tmp_path / "download").rglob("*.part-*"))
    finally:
        server.shutdown()
        thread.join()


def test_download_resumes_stable_partial_file(tmp_path: Path) -> None:
    class Response:
        def __init__(self, status: int, chunks: list[bytes], headers: dict[str, str]) -> None:
            self.status = status
            self._chunks = list(chunks)
            self.headers = headers

        def read(self, _size: int) -> bytes:
            if not self._chunks:
                raise URLError("connection interrupted")
            return self._chunks.pop(0)

        def close(self) -> None:
            return None

    service = DownloadService(tmp_path / "download", Source("https://example.test/file"))
    calls: list[dict[str, str] | None] = []
    first = True

    def open_response(_url: str, *, headers: dict[str, str] | None = None) -> Response:
        nonlocal first
        calls.append(headers)
        if first:
            first = False
            return Response(200, [b"abc"], {"Content-Length": "10"})
        return Response(
            206,
            [b"defghij", b""],
            {"Content-Range": "bytes 3-9/10", "Content-Length": "7"},
        )

    service._open = open_response  # type: ignore[method-assign]
    with pytest.raises(DownloadFailed):
        service.download(7, 0)
    partial = tmp_path / "download" / "Web" / "7-shopping" / ".shopping.zip.part"
    assert partial.read_bytes() == b"abc"

    result = service.download(7, 0)
    assert result.size == 10
    assert (tmp_path / "download" / "Web" / "7-shopping" / "shopping.zip").read_bytes() == b"abcdefghij"
    assert calls == [None, {"Range": "bytes=3-"}]


def test_attachment_batch_catalog_clear_redownload_and_web_routes(
    tmp_path: Path,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = {"/a.txt": b"A", "/b.txt": b"B"}.get(self.path, b"")
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"

        class BatchSource:
            groups = [
                ExerciseGroup(
                    1,
                    "Web",
                    1,
                    (ExerciseSummary(7, "shopping"), ExerciseSummary(8, "empty")),
                )
            ]

            details = {
                7: ExerciseDetail(
                    7,
                    "shopping",
                    attachment=Attachment(
                        (
                            File("a.txt", f"{origin}/a.txt", "txt"),
                            File("b.txt", f"{origin}/b.txt", "txt"),
                        )
                    ),
                ),
                8: ExerciseDetail(8, "empty"),
            }

            def exercises(self, refresh: bool = False) -> list[ExerciseGroup]:
                return self.groups

            def exercise(
                self, exercise_id: int, refresh: bool = False
            ) -> ExerciseDetail:
                return self.details[exercise_id]

        source = BatchSource()
        service = DownloadService(tmp_path / "download", source)
        tasks = AttachmentTaskManager(service)
        initial = service.catalog()
        assert initial.total_exercises == 2
        assert initial.total_attachments == 2
        assert initial.existing_attachments == 0

        one = service.download(7, 0)
        assert one.existed is False and one.size == 1
        batch = service.download_exercise(7)
        assert (batch.total, batch.downloaded, batch.existing, batch.failed) == (
            2,
            1,
            1,
            0,
        )
        assert service.attachments(7)[1].exists is True

        existing = service.catalog()
        assert existing.existing_attachments == 2
        all_existing = service.download_all()
        assert (all_existing.total, all_existing.downloaded, all_existing.existing) == (
            2,
            0,
            2,
        )

        cleared = service.clear_exercise(7)
        assert (cleared.removed_files, cleared.removed_bytes) == (2, 2)
        assert not (tmp_path / "download" / "Web" / "7-shopping").exists()
        redownloaded = service.redownload_exercise(7)
        assert redownloaded.download.downloaded == 2

        app = create_app(source, service, None, None, service, attachment_tasks=tasks)
        trusted = {"X-GCSIS-Action": "attachment-manager"}
        with TestClient(app) as client:
            response = client.get("/api/exercises/7")
            assert response.status_code == 200
            detail_file = response.json()["data"]["attachment"]["files"][0]
            assert detail_file["size"] == 1
            assert detail_file["exists"] is True

            response = client.get("/api/attachments")
            assert response.status_code == 200
            assert response.json()["data"]["existingAttachments"] == 2

            response = client.post("/api/exercises/7/attachments/0/download")
            assert response.status_code == 200
            assert response.json()["data"]["existed"] is True

            response = client.post("/api/attachments/download-all")
            assert response.status_code == 403
            response = client.post("/api/attachments/download-all", headers=trusted)
            assert response.status_code == 200
            assert response.json()["data"]["existing"] == 2

            response = client.delete("/api/exercises/7/attachments", headers=trusted)
            assert response.status_code == 200
            assert response.json()["data"]["removedFiles"] == 2
            response = client.post(
                "/api/exercises/7/attachments/redownload", headers=trusted
            )
            assert response.status_code == 200
            assert response.json()["data"]["download"]["downloaded"] == 2

            response = client.post(
                "/api/attachments/downloads",
                headers=trusted,
                json={"scope": "all"},
            )
            assert response.status_code == 202
            task_id = response.json()["data"]["id"]
            for _ in range(100):
                task_response = client.get(f"/api/attachments/downloads/{task_id}")
                assert task_response.status_code == 200
                if task_response.json()["data"]["status"] != "running":
                    break
                time.sleep(0.01)
            assert task_response.json()["data"]["status"] == "completed"
            response = client.post(
                f"/api/attachments/downloads/{task_id}/pause"
            )
            assert response.status_code == 403
            response = client.get(
                f"/api/attachments/downloads/{task_id}/pause", headers=trusted
            )
            assert response.status_code == 405
            response = client.post(
                f"/api/attachments/downloads/{task_id}/pause", headers=trusted
            )
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "completed"

            response = client.post("/api/attachments/sizes", headers=trusted)
            assert response.status_code == 202
            assert response.json()["data"]["status"] in {"running", "completed"}
    finally:
        server.shutdown()
        thread.join()


class AttachmentsStub:
    def attachments(self, _id: int) -> list[AttachmentInfo]:
        return [AttachmentInfo(0, "a.txt", "download/Web/7-shopping/a.txt", True, 1)]

    def download(self, _id: int, _index: int):
        return type(
            "Result",
            (),
            {"to_dict": lambda self: {"path": "a", "size": 1, "existed": False}},
        )()

    def inspect(self, _id: int, _index: int):
        return type("Inspection", (), {"to_dict": lambda self: {"preview": "a"}})()


def test_solver_accepts_a_matching_exercise_in_an_empty_category(
    tmp_path: Path,
) -> None:
    source = Source()
    source.groups = [ExerciseGroup(1, "", 1, (ExerciseSummary(7, "shopping"),))]
    service = SolverService(
        source, AttachmentsStub(), None, "", tmp_path / "writeups", tmp_path
    )
    result = service.prompt(7)
    assert result.exercise_id == 7
    assert "题目类型：" in result.prompt


def test_solver_normalizes_flag_and_writes_wp(tmp_path: Path) -> None:
    source = Source()

    class Model:
        def __init__(self) -> None:
            self.calls = 0

        def complete(
            self, _model: str, _messages: list[Message], _tools: list[Any]
        ) -> Message:
            self.calls += 1
            if self.calls == 1:
                call = ToolCall(
                    "submit",
                    "function",
                    FunctionCall("submit_flag", '{"flag":"ctf{ answer }"}'),
                )
                return Message("assistant", tool_calls=(call,))
            return Message("assistant", "# WP")

    service = SolverService(
        source, AttachmentsStub(), Model(), "model", tmp_path / "writeups", tmp_path
    )
    result = service.run(7)
    assert result.solved is True
    assert result.flag == "answer"
    assert source.submitted == ["answer"]
    assert (tmp_path / "writeups" / "Web" / "7-shopping.md").read_text(
        encoding="utf-8"
    ) == "# WP"
    assert normalize_flag("flag{x}") == "x"


def test_web_routes_preserve_envelopes_and_attachment_guard() -> None:
    source = Source()
    app = create_app(source, None, None, None)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "img-src 'self' data: https:" in page.headers["Content-Security-Policy"]
        assert page.headers["X-Content-Type-Options"] == "nosniff"

        response = client.get("/api/overview")
        assert response.status_code == 200 and response.json()["data"]["stageRank"] == 1
        response = client.post("/api/overview", json={})
        assert response.status_code == 405
        response = client.get("/api/unknown")
        assert (
            response.status_code == 404
            and response.json()["error"]["code"] == "NOT_FOUND"
        )


def test_web_preserves_specific_flag_and_model_error_codes() -> None:
    app = create_app(Source(), None, None, None)
    with TestClient(app) as client:
        response = client.post("/api/exercises/7/answer", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_FLAG"

        response = client.post("/api/exercises/7/answer", json={"flag": "界" * 257})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_FLAG"

    class AI:
        def enabled(self) -> bool:
            return True

        def run(self, _exercise_id: int) -> None:
            raise ModelNotConfigured("disabled")

    app = create_app(Source(), None, AI(), None)
    with TestClient(app) as client:
        response = client.post("/api/exercises/7/ai/run")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"


def test_attachment_catalog_reports_unconfigured_platform(tmp_path: Path) -> None:
    class UnconfiguredSource:
        def exercises(self, refresh: bool = False) -> list[ExerciseGroup]:
            raise PlatformNotConfigured("比赛尚未绑定")

        def exercise(self, exercise_id: int, refresh: bool = False) -> ExerciseDetail:
            raise PlatformNotConfigured("比赛尚未绑定")

    source = UnconfiguredSource()
    service = DownloadService(tmp_path / "download", source)
    app = create_app(source, service, None, None, service)
    with TestClient(app) as client:
        response = client.get("/api/attachments")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PLATFORM_NOT_CONFIGURED"


def test_web_preserves_go_route_bounds_and_trailing_slash_behavior() -> None:
    class RouteSource(Source):
        def notice(self, notice_id: int, _refresh: bool = False) -> dict[str, int]:
            return {"id": notice_id}

    app = create_app(RouteSource(), None, None, None)
    with TestClient(app) as client:
        response = client.get("/api/notices/501/", follow_redirects=False)
        assert response.status_code == 200
        assert response.json()["data"]["id"] == 501

        response = client.get("/api/exercises/9223372036854775808")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_ID"

        response = client.post(
            "/api/exercises/7/attachments/9223372036854775808/download"
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_ATTACHMENT_INDEX"

        response = client.get("/api/codex/tasks/", follow_redirects=False)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


def test_web_distinguishes_invalid_and_untrusted_request_bodies() -> None:
    app = create_app(Source(), None, None, None)
    trusted_headers = {"X-GCSIS-Action": "attachment-manager"}
    with TestClient(app) as client:
        response = client.post(
            "/api/attachments/download-all",
            content=b"x" * 2049,
            headers=trusted_headers,
        )
        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "INVALID_REQUEST",
            "message": "请求体无效",
        }

        response = client.post(
            "/api/attachments/download-all", content=b"{}", headers=trusted_headers
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UNTRUSTED_INPUT"

        response = client.post("/api/exercises/7/answer", content=b'{"flag":"ok"}{}')
        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "INVALID_JSON",
            "message": "Flag 请求只能包含一个 JSON 对象",
        }


def test_codex_manager_runs_and_writes_output(tmp_path: Path) -> None:
    class Prompt:
        def prompt(self, exercise_id: int):
            from internal.solver import PromptResult

            return PromptResult("prompt", False, "", exercise_id, "Web", "x")

        def prompt_pure(self, exercise_id: int):
            return self.prompt(exercise_id)

    class Runner:
        def run(
            self, config: ProcessConfig, _prompt: str, on_event, _cancel
        ) -> ProcessResult:
            Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(config.output_path).write_text("done", encoding="utf-8")
            on_event(Event(datetime.now(timezone.utc), "message", "ok"))
            return ProcessResult(0, "done", "session")

    manager = CodexManager(
        ManagerConfig(
            ProcessConfig("codex", str(tmp_path), "http://model", model="m"),
            Prompt(),
            Runner(),
            1,
            tmp_path / "runs",
            tmp_path / "writeups",
            tmp_path,
        )
    )
    snapshot = manager.start(7)
    for _ in range(50):
        if manager.get(snapshot.id).status == "completed":
            break
        time.sleep(0.01)
    completed = manager.get(snapshot.id)
    assert completed.output == "done"
    assert completed.session_id == "session"
    manager.close()


def test_codex_task_metadata_survives_restart_without_api_key(tmp_path: Path) -> None:
    class Prompt:
        def prompt(self, exercise_id: int):
            from internal.solver import PromptResult

            return PromptResult("prompt", False, "", exercise_id, "Web", "x")

    class Runner:
        def run(self, config: ProcessConfig, _prompt: str, on_event, _cancel) -> ProcessResult:
            Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(config.output_path).write_text("done", encoding="utf-8")
            on_event(Event(datetime.now(timezone.utc), "message", "persisted"))
            return ProcessResult(0, "done", "session-persisted")

    tasks_path = tmp_path / "runtime" / "codex" / "tasks.json"
    config = ManagerConfig(
        ProcessConfig("codex", str(tmp_path), "https://model.example.test/v1", "secret-key", "solver"),
        Prompt(),
        Runner(),
        1,
        tmp_path / "runs",
        tmp_path / "writeups",
        tmp_path,
        tasks_path=tasks_path,
    )
    manager = CodexManager(config)
    task = manager.start(7)
    for _ in range(100):
        if manager.get(task.id).status == "completed":
            break
        time.sleep(0.01)
    manager.close()
    persisted = tasks_path.read_text(encoding="utf-8")
    assert "secret-key" not in persisted

    restored = CodexManager(config)
    recovered = restored.get(task.id)
    assert recovered.status == "completed"
    assert recovered.session_id == "session-persisted"
    assert recovered.events[0].summary == "persisted"
    restored.close()


def test_codex_requeues_persisted_tasks_when_configuration_becomes_available(
    tmp_path: Path,
) -> None:
    class Runner:
        def __init__(self) -> None:
            self.called = threading.Event()

        def run(self, config: ProcessConfig, _prompt: str, _on_event, _cancel) -> ProcessResult:
            Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(config.output_path).write_text("recovered", encoding="utf-8")
            self.called.set()
            return ProcessResult(0, "recovered", "session-recovered")

    task_id = "a" * 24
    tasks_path = tmp_path / "runtime" / "codex" / "tasks.json"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": [
                    {
                        "snapshot": {
                            "id": task_id,
                            "exerciseId": 7,
                            "mode": "full",
                            "status": "queued",
                            "limit": 1,
                            "createdAt": "2026-09-13T00:00:00Z",
                        },
                        "prompt": "恢复任务",
                        "config": {
                            "baseUrl": "https://model.example.test/v1",
                            "model": "solver",
                            "outputPath": str(tmp_path / "runs" / task_id / "final.md"),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = Runner()
    class Prompt:
        def prompt(self, _exercise_id: int):
            raise AssertionError("restored task should not rebuild its prompt")

    manager = CodexManager(
        ManagerConfig(
            ProcessConfig("", str(tmp_path), "", model=""),
            prompt=Prompt(),
            runner=runner,
            max_concurrency=1,
            runs_root=tmp_path / "runs",
            writeup_root=tmp_path / "writeups",
            workspace_root=tmp_path,
            tasks_path=tasks_path,
        )
    )
    assert manager.get(task_id).status == "queued"

    manager.configure(
        ProcessConfig(
            "codex",
            str(tmp_path),
            "https://model.example.test/v1",
            model="solver",
        )
    )
    assert runner.called.wait(timeout=2)
    for _ in range(100):
        if manager.get(task_id).status == "completed":
            break
        time.sleep(0.01)
    assert manager.get(task_id).status == "completed"
    manager.close()


def test_codex_tasks_over_limit_are_queued_without_pausing_running_task(
    tmp_path: Path,
) -> None:
    class Prompt:
        def prompt(self, exercise_id: int):
            from internal.solver import PromptResult

            return PromptResult("prompt", False, "", exercise_id, "Web", "x")

        def prompt_pure(self, exercise_id: int):
            return self.prompt(exercise_id)

    class Runner:
        def __init__(self) -> None:
            self.first_started = threading.Event()
            self.release_first = threading.Event()
            self.calls = 0

        def run(
            self, config: ProcessConfig, _prompt: str, _on_event, _cancel
        ) -> ProcessResult:
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                self.release_first.wait(timeout=2)
            Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(config.output_path).write_text("done", encoding="utf-8")
            return ProcessResult(0, "done", f"session-{self.calls}")

    runner = Runner()
    manager = CodexManager(
        ManagerConfig(
            ProcessConfig("codex", str(tmp_path), "http://model", model="m"),
            Prompt(),
            runner,
            1,
            tmp_path / "runs",
            tmp_path / "writeups",
            tmp_path,
        )
    )
    first = manager.start(7)
    assert runner.first_started.wait(timeout=1)
    second = manager.start(8)
    assert manager.get(first.id).status == "running"
    assert manager.get(second.id).status == "queued"
    assert manager.list().active == 1
    runner.release_first.set()
    for _ in range(100):
        if manager.get(second.id).status == "completed":
            break
        time.sleep(0.01)
    assert manager.get(second.id).status == "completed"
    assert runner.calls == 2
    manager.close()


def test_codex_command_supports_resume_and_fork(tmp_path: Path) -> None:
    base = ProcessConfig(
        "codex",
        str(tmp_path),
        "https://model.example.test/v1",
        model="solver",
        output_path=str(tmp_path / "final.md"),
    )
    resume = build_command(
        ProcessConfig(**{**base.__dict__, "resume_session_id": "session-1"})
    )
    fork = build_command(
        ProcessConfig(
            **{**base.__dict__, "resume_session_id": "session-1", "fork_session": True}
        )
    )
    assert resume[1:3] == ["exec", "resume"]
    assert resume[3] == "session-1"
    assert fork[1:3] == ["exec", "fork"]
    assert fork[3] == "session-1"
    for command in (resume, fork):
        assert "--sandbox" not in command
        assert "--cd" not in command
        assert 'sandbox_mode="danger-full-access"' in command


def test_codex_interactive_resume_uses_only_supported_options(tmp_path: Path) -> None:
    command = build_interactive_command(
        ProcessConfig(
            "codex",
            str(tmp_path),
            "https://model.example.test/v1",
            model="solver",
        ),
        "session-1",
    )
    assert command[-2:] == ["resume", "session-1"]
    assert "--ignore-user-config" not in command
    assert "--skip-git-repo-check" not in command
    assert command.count("resume") == 1


def test_codex_macos_terminal_opens_one_command_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "open-terminal.command"
    script.write_text("#!/bin/zsh\n", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> None:
        calls.append((command, kwargs))

    monkeypatch.setattr("internal.codex.sys.platform", "darwin")
    monkeypatch.setattr("internal.codex.subprocess.run", fake_run)

    _launch_system_terminal(script, tmp_path)

    assert len(calls) == 1
    command, options = calls[0]
    assert command == ["/usr/bin/open", "-a", "Terminal", str(script)]
    assert options["cwd"] == str(tmp_path)
    assert options["check"] is True
    assert options["timeout"] == 15


def test_codex_follow_up_and_side_create_expected_sessions(tmp_path: Path) -> None:
    class Prompt:
        def prompt(self, exercise_id: int):
            from internal.solver import PromptResult

            return PromptResult("prompt", False, "", exercise_id, "Web", "x")

        def prompt_pure(self, exercise_id: int):
            return self.prompt(exercise_id)

    class Runner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool, str]] = []

        def run(
            self, config: ProcessConfig, prompt: str, _on_event, _cancel
        ) -> ProcessResult:
            self.calls.append((config.resume_session_id, config.fork_session, prompt))
            Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(config.output_path).write_text("done", encoding="utf-8")
            return ProcessResult(0, prompt, "session-1")

    runner = Runner()
    manager = CodexManager(
        ManagerConfig(
            ProcessConfig("codex", str(tmp_path), "http://model", model="m"),
            Prompt(),
            runner,
            1,
            tmp_path / "runs",
            tmp_path / "writeups",
            tmp_path,
        )
    )
    parent = manager.start(7)
    for _ in range(50):
        if manager.get(parent.id).status == "completed":
            break
        time.sleep(0.01)
    assert manager.follow_up(parent.id, "继续分析").status == "queued"
    for _ in range(50):
        if manager.get(parent.id).status == "completed":
            break
        time.sleep(0.01)
    side = manager.side(parent.id, "解释思路")
    assert side.parent_id == parent.id
    for _ in range(50):
        if manager.get(side.id).status == "completed":
            break
        time.sleep(0.01)
    assert manager.get(side.id).mode == "side"
    assert any(resume == "session-1" and not fork for resume, fork, _ in runner.calls)
    assert any(resume == "session-1" and fork for resume, fork, _ in runner.calls)
    manager.close()


def test_codex_open_terminal_writes_workspace_local_resume_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Prompt:
        def prompt(self, exercise_id: int):
            from internal.solver import PromptResult

            return PromptResult("prompt", False, "", exercise_id, "Web", "x")

        def prompt_pure(self, exercise_id: int):
            return self.prompt(exercise_id)

    class Runner:
        def run(self, config: ProcessConfig, _prompt: str, _on_event, _cancel) -> ProcessResult:
            Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(config.output_path).write_text("done", encoding="utf-8")
            return ProcessResult(0, "done", "session-1")

    manager = CodexManager(
        ManagerConfig(
            ProcessConfig("codex", str(tmp_path), "https://model.example.test/v1", model="solver"),
            Prompt(),
            Runner(),
            1,
            tmp_path / "runs",
            tmp_path / "writeups",
            tmp_path,
        )
    )
    task = manager.start(7)
    for _ in range(50):
        if manager.get(task.id).status == "completed":
            break
        time.sleep(0.01)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        "internal.codex._launch_system_terminal",
        lambda script, workspace: launched.append((script, workspace)),
    )
    result = manager.open_terminal(task.id)
    script = tmp_path / str(result["scriptPath"])
    content = script.read_text(encoding="utf-8")
    assert result["opened"] is True
    assert launched == [(script, tmp_path)]
    assert "CODEX_HOME=" in content
    assert "codex" in content and "resume session-1" in content
    assert "--ignore-user-config" not in content
    assert "--skip-git-repo-check" not in content

    opened_paths: list[Path] = []
    monkeypatch.setattr(
        "internal.codex._open_system_path", opened_paths.append
    )
    folder_result = manager.open_challenge_folder(task.id)
    challenge_directory = tmp_path / "download" / "Web" / "7-x"
    assert folder_result == {"opened": True, "path": "download/Web/7-x"}
    assert opened_paths == [challenge_directory]
    assert challenge_directory.is_dir()
    manager.close()


def test_codex_legacy_folder_recovery_and_download_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = "a" * 24
    tasks_path = tmp_path / "runtime" / "codex" / "tasks.json"
    tasks_path.parent.mkdir(parents=True)
    tasks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": [
                    {
                        "snapshot": {
                            "id": task_id,
                            "exerciseId": 7,
                            "mode": "full",
                            "status": "completed",
                            "writeupPath": f"writeups/Web/7-x-codex-{task_id}.md",
                            "sessionId": "session-1",
                            "createdAt": "2026-09-13T00:00:00Z",
                        },
                        "prompt": "legacy task",
                        "config": {
                            "baseUrl": "https://model.example.test/v1",
                            "model": "solver",
                            "outputPath": str(tmp_path / "runs" / task_id / "final.md"),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manager = CodexManager(
        ManagerConfig(
            ProcessConfig("codex", str(tmp_path), "https://model.example.test/v1"),
            prompt=None,
            runner=None,
            max_concurrency=1,
            runs_root=tmp_path / "runs",
            writeup_root=tmp_path / "writeups",
            workspace_root=tmp_path,
            tasks_path=tasks_path,
        )
    )
    opened_paths: list[Path] = []
    monkeypatch.setattr("internal.codex._open_system_path", opened_paths.append)

    result = manager.open_challenge_folder(task_id)

    expected = tmp_path / "download" / "Web" / "7-x"
    assert result == {"opened": True, "path": "download/Web/7-x"}
    assert opened_paths == [expected]

    task = manager._tasks[task_id]
    task.snapshot = task.snapshot.__class__(
        **{**task.snapshot.__dict__, "challenge_path": "../outside"}
    )
    with pytest.raises(CodexError, match="download"):
        manager.open_challenge_folder(task_id)
    assert opened_paths == [expected]
    manager.close()


def test_codex_manager_applies_original_constructor_defaults(tmp_path: Path) -> None:
    manager = CodexManager(
        ManagerConfig(
            process=ProcessConfig("", str(tmp_path), ""),
            prompt=None,
            runner=None,
            max_concurrency=0,
            runs_root="",
            writeup_root="",
        )
    )
    assert manager.limit == DEFAULT_CODEX_MAX_CONCURRENCY == 5
    assert manager.runs_root == Path("codex-runs")
    assert manager.writeup_root == Path("writeups")
    assert manager.workspace_root == tmp_path
    manager.close()


def test_codex_process_uses_workspace_local_home(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = ProcessConfig(
        "codex",
        str(workspace),
        "https://model.example.test/v1",
        "not-a-real-key",
        "solver",
        str(workspace / "codex-runs" / "task" / "final.md"),
        str(workspace / ".codex-home"),
    )

    codex_home = _prepare_codex_home(config)
    environment = _process_environment(config.api_key, codex_home)
    command = build_command(config)

    assert codex_home == str(workspace / ".codex-home")
    assert (workspace / ".codex-home").stat().st_mode & 0o777 == 0o700
    assert environment["CODEX_HOME"] == codex_home
    assert environment["GCSIS_CODEX_API_KEY"] == "not-a-real-key"
    assert "--ignore-user-config" in command


def test_codex_process_rejects_home_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = ProcessConfig(
        "codex",
        str(workspace),
        "https://model.example.test/v1",
        home=str(tmp_path / "outside"),
    )

    with pytest.raises(CodexError, match="inside the workspace"):
        _prepare_codex_home(config)
