"""Application entry point for the Python CTF workbench."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import FastAPI

from internal.attachment_tasks import AttachmentTaskManager
from internal.cached import CachedService
from internal.codex import (
    CodexManager,
    ManagerConfig,
    ProcessConfig,
    ProcessRunner,
    prepare_model_catalog,
)
from internal.codex_skills import prepare_codex_skills
from internal.config import Config, RuntimeConfigStore, load
from internal.download import DownloadService
from internal.events import EventBroker
from internal.ichunqiu import AgentClient
from internal.model_router import ModelRouter
from internal.solver import SolverService
from internal.tool_manager import ToolManager
from internal.web import create_app

LOGGER = logging.getLogger("gcsis-tools")


def build_application(config: Config | None = None) -> FastAPI:
    """Build the ASGI app and all services using only paths below the workspace."""

    workspace = Path.cwd().absolute()
    cfg = config or load(workspace / ".env")
    runtime_root = workspace / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    config_path = runtime_root / "config.json"
    legacy_config = workspace / ".runtime-config.json"
    if not config_path.exists() and legacy_config.is_file():
        try:
            shutil.copy2(legacy_config, config_path)
            config_path.chmod(0o600)
        except OSError:
            LOGGER.warning("无法迁移旧运行时配置，将继续使用新的配置路径")
    config_store = RuntimeConfigStore(cfg, config_path)
    runtime = config_store.get()
    upstream = AgentClient(
        timeout=15.0,
        config_provider=config_store.get,
        schema_path=runtime_root / "platform-schema.json",
    )
    cached = CachedService(upstream)
    downloads = DownloadService(workspace / "download", cached, timeout=300.0)
    event_broker = EventBroker()
    attachment_tasks = AttachmentTaskManager(
        downloads, event_publisher=event_broker.publish
    )
    tools = ToolManager(workspace, event_publisher=event_broker.publish)
    model = ModelRouter(runtime)
    solver = SolverService(
        cached, downloads, model, "auto", workspace / "writeups", workspace
    )
    binary = shutil.which("codex") or ""
    codex_home = runtime_root / "codex"
    legacy_codex_home = workspace / ".codex-home"
    if not codex_home.exists() and legacy_codex_home.exists():
        try:
            legacy_codex_home.rename(codex_home)
        except OSError:
            try:
                shutil.copytree(legacy_codex_home, codex_home, dirs_exist_ok=True)
            except OSError:
                LOGGER.warning("无法迁移旧 Codex 数据目录，将继续使用新的目录")
    prepare_codex_skills(workspace, codex_home, runtime.codex_ctf_skills_enabled)
    model_catalog_path = prepare_model_catalog(
        binary,
        codex_home / "model-catalog.json",
        runtime.codex_model,
        ("deepseek-v4-flash",),
        runtime.codex_auto_compact_token_limit,
    )
    process_config = ProcessConfig(
        binary,
        str(workspace),
        runtime.codex_base_url,
        runtime.codex_api_key,
        runtime.codex_model,
        home=str(codex_home),
        protocol=runtime.codex_protocol,
        model_catalog_path=model_catalog_path,
    )
    codex = CodexManager(
        ManagerConfig(
            process=process_config,
            prompt=solver,
            runner=ProcessRunner(binary),
            max_concurrency=runtime.codex_max_concurrency,
            runs_root=workspace / "codex-runs",
            writeup_root=workspace / "writeups",
            workspace_root=workspace,
            system_prompt=runtime.codex_system_prompt,
            tasks_path=runtime_root / "codex" / "tasks.json",
            auto_resume_interrupted=runtime.codex_auto_resume_interrupted,
            event_publisher=event_broker.publish,
        )
    )
    app = create_app(
        cached,
        downloads,
        solver,
        codex,
        downloads,
        workspace / "static",
        config_store,
        attachment_tasks,
        tools,
        environment_workspace=workspace,
        event_broker=event_broker,
    )
    app.state.config = config_store.get()
    app.state.config_store = config_store

    def apply_runtime_config(updated: Config) -> None:
        cached.invalidate()
        attachment_tasks.reset_probe()
        model.configure(updated)
        solver.configure_model(model, "auto")
        prepare_codex_skills(workspace, codex_home, updated.codex_ctf_skills_enabled)
        model_catalog_path = prepare_model_catalog(
            binary,
            codex_home / "model-catalog.json",
            updated.codex_model,
            ("deepseek-v4-flash",),
            updated.codex_auto_compact_token_limit,
        )
        codex.configure(
            ProcessConfig(
                binary,
                str(workspace),
                updated.codex_base_url,
                updated.codex_api_key,
                updated.codex_model,
                home=str(codex_home),
                protocol=updated.codex_protocol,
                model_catalog_path=model_catalog_path,
            ),
            updated.codex_max_concurrency,
            updated.codex_system_prompt,
            updated.codex_auto_resume_interrupted,
        )

    config_store.subscribe(apply_runtime_config)
    app.state.codex_manager = codex
    # FastAPI 0.141 exposes lifecycle callbacks on the router; keeping the
    # manager close hook here also works with older FastAPI releases.
    app.router.on_shutdown.append(codex.close)
    return app


def _listen_parts(value: str) -> tuple[str, int]:
    raw = value.strip()
    if raw.startswith("["):
        closing_bracket = raw.find("]")
        if (
            closing_bracket <= 1
            or raw[closing_bracket + 1 : closing_bracket + 2] != ":"
        ):
            raise ValueError("listenaddr must be in host:port form")
        host = raw[1:closing_bracket]
        port = raw[closing_bracket + 2 :]
    else:
        if raw.count(":") != 1:
            raise ValueError("listenaddr must be in host:port form")
        host, port = raw.rsplit(":", 1)
    return host or "0.0.0.0", int(port)


def run() -> None:
    import uvicorn

    cfg = load(Path.cwd() / ".env")
    app = build_application(cfg)
    host, port = _listen_parts(cfg.listen_addr)
    LOGGER.info("CTF web console listening at http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run()
