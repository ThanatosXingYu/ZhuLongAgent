# i春秋 CTF 比赛工作台（Python）

一个面向 i春秋比赛平台的本地 CTF 工作台。项目使用 FastAPI 提供本地 Web 控制台，保留比赛页面中的题目、公告、成绩、队伍信息、附件、容器和 Flag 提交能力，并提供可选的 OpenAI 兼容模型调用与本机 Codex CLI 任务。

## 功能概览

- 绑定 `https://match.ichunqiu.com/wanqubei` 或带 `k` 参数的比赛入口。
- 支持已有登录 Token、密码登录和短信验证码登录；旋转验证码由用户在页面中手动完成。
- 加载题目、题目详情、分类、附件、公告、比赛规则、队伍信息、得分与排名。
- 附件大小探测、单题/分类/全部下载，支持暂停、继续、取消，并通过 SSE 实时显示文件大小、已传输量、速度和预计剩余时间；断线时保留低频轮询降级。
- 仅对平台明确提供环境能力的题目显示“启动容器”；不对普通附件题误显示容器按钮。
- CTF 工具管理：按 Python 库、Go 开源项目、独立工具/发行包分组，区分已安装与待安装工具，安装任务支持取消和 SSE 实时进度。
- 工具安装会并行测速官方 PyPI/Go/GitHub 与公开加速地址，优先选择延迟最低的来源；失败时自动回退官方地址。GitHub 加速只用于下载/克隆，API 请求仍使用官方接口。
- 本地环境检测：在工具管理页检测 Python、Java、Go、Rust、GCC、GDB、NASM、Node.js、PHP、Perl、Ruby、Git 等运行时。
- Codex CLI 任务：主任务、纯用户提问的独立 Side 分支、追加输入、继续、停止、删除、分轮日志和解题报告；诊断信息会区分黄色警告与红色错误，任务及日志变化通过 SSE 实时推送。
- 全部运行数据、下载文件、工具缓存和 Codex 数据均限制在当前项目目录，不启动或修改 Docker。

## 快速开始

要求：Python 3.12 或更高版本。项目不需要 Docker。

```bash
# 1. 创建工作台自身的虚拟环境
python3 -m venv .venv

# 2. 将 pip 下载缓存放在项目目录的 tools/cache/pip
PIP_CACHE_DIR="$PWD/tools/cache/pip" .venv/bin/python -m pip install -r requirements.txt

# 3. 启动本地 Web 服务
.venv/bin/python main.py
```

启动后访问 <http://127.0.0.1:8080>。如果需要监听其他地址，可在项目根目录创建 `.env`：

```dotenv
listenaddr=127.0.0.1:8080
```

`.env` 只用于监听地址。比赛地址、登录态、模型和 Codex 配置在 Web 页面中填写，不从 `.env` 读取。

## 平台绑定与登录

1. 打开右上角“设置”，输入比赛地址。
2. 如果已有登录 Token，可以在“已有登录 Token”中粘贴；Token 只写入当前目录的运行时配置，不会在页面回显。
3. 如果没有可用 Token，先绑定比赛，再选择密码登录或短信验证码登录。
4. 短信登录需要先拖动旋转验证码，将图片中的人物或文字调整为自然竖直方向，再发送短信验证码并输入验证码。

账号、密码、图形验证码和短信验证码只在请求期间使用，不会持久化。登录成功后，题目、公告、比赛说明、队伍和成绩会自动刷新。

## 工具管理

点击首页顶部的“工具管理”打开管理页。页面分为三类：

- **Python 库**：安装到 `tools/venv`，入口同步暴露到 `tools/bin`，例如 `pwntools`、`z3-solver`、`angr`、`scapy`。
- **Go 开源项目**：使用本机 Go 编译，`GOPATH`、`GOMODCACHE`、`GOCACHE` 和 `GOBIN` 全部重定向到 `tools/go` 或 `tools/bin`，例如 `ffuf`、`gobuster`。
- **独立工具 / 发行包**：下载并解压到 `tools/`，例如 `jadx` 和 ExifTool。ExifTool 使用官方 GitHub 镜像的版本标签下载，避免 exiftool.org 站点对归档路径返回 404。

Python 库列表中的“基础库”是一个独立工具卡片，会一次安装 `requests`、`gmpy2` 和 `pycryptodome`；`json`、`base64`、`hashlib`、`re`、`struct`、`urllib` 等属于 Python 标准库，不需要 pip 安装。独立工具还包括 checksec、Apktool、7-Zip、foremost、John the Ripper、Hashcat、GDB 和 pwndbg。部分工具以源码目录保存，是否能在当前操作系统编译取决于本机编译器和上游项目支持，程序不会调用 Homebrew、apt 或 Docker。

工具卡片会显示支持平台并根据当前系统禁用不适用的项目。当前目录中的平台策略如下：

| 工具 | 支持平台 / 备注 |
| --- | --- |
| Python 基础库、sqlmap、dirsearch、z3、scapy、pyelftools、Capstone、Unicorn、SymPy 等 | Linux、macOS、Windows（以 Python/轮子实际支持为准） |
| ffuf、Gobuster | Linux、macOS、Windows（Go 工具链） |
| jadx、Apktool、ExifTool | Linux、macOS、Windows；分别需要 Java、对应平台发布包或平台对应的 ExifTool 包 |
| 7-Zip、John、Hashcat、GDB、pwndbg | 页面可下载源码副本；不会自动编译，实际运行需要按项目说明在对应平台构建（pwndbg 建议 Linux/WSL） |
| checksec、foremost | Linux、macOS；Windows 请使用 WSL 或其他兼容环境 |
| pwntools | Linux、macOS；Windows 请使用 WSL |
| GDB | Linux、Windows；macOS 需要额外代码签名，页面会禁用自动安装 |
| pwndbg | Linux；Windows 请使用 WSL，且依赖可用的 GDB |

Ropper 在 Python 3.14 上游依赖尚未兼容，页面会提前标记为不可安装；使用 Python 3.12 或 3.13 的工作区时再安装。工具页中的“源码已获取”只表示源码归档完整落盘，不代表已经生成了可执行命令。

源码类项目的“安装”表示源码归档已下载并完整保存在 `tools/src/`，不会把未验证的系统命令误标为可用；安装完成后才会写入完成标记，卸载会删除对应源码目录和项目内入口。

GitHub 项目下载会在官方地址与公开加速地址之间做短连接测速（例如 `ghfast.top`、`gh-proxy.com`、`ghproxy.net`），每次实际下载仍保留官方地址作为回退。加速服务不可用或返回错误时不会阻塞安装；下载的源码、归档、Git 缓存和构建产物都放在 `tools/`。

工具安装不会写入系统 Python、系统 Go 缓存或用户级缓存。安装过程会显示当前工具、已完成数量、当前文件大小、已传输量、速度和预计剩余时间；当上游安装器无法提供总大小时，进度条会明确显示“总大小未知”，速度仍按本地缓存增长量测量。安装任务可以随时取消，已经完成的工具会保留。

本地环境检测位于工具管理页的“本地运行环境”面板。它是只读检查，不会为了检测自动安装任何运行时；每个环境都会显示可用状态、版本和可执行文件路径。

## Codex CLI 与技能

设置页中的“快捷 AI 调用”通过 Chat Completions 接口执行一次性分析；“Codex CLI 任务”启动本机 `codex` 进程，通过 Responses 兼容接口执行带日志的完整任务。两组地址、模型和 Key 相互独立，可以按需要填写相同或不同的值。

Codex 默认最多同时运行 10 个任务。超过上限的新任务会显示为“排队”，前面的运行中任务不会被暂停或取消；任务完成后，队列会自动按顺序继续执行。并发上限可在设置页调整为 `1` 到 `16`。Side 提问会创建与父任务关联、但只发送当前用户问题的新会话，不会重复附加题目提示词或全局系统提示词。

任务元数据会原子保存到 `runtime/codex/tasks.json`。程序重启后，已完成、已停止、排队中的任务及其运行日志、会话 ID 和 WP 路径都会恢复；重启时无法继续存在的运行中任务会标记为“程序重启时任务中断”，可使用“继续”恢复。该文件只保存任务元数据，不保存 Codex API Key。

项目内置 [`ljagiello/ctf-skills`](https://github.com/ljagiello/ctf-skills) 技能源，位于 `tools/skills/ctf-skills`，默认启用。设置页可以关闭技能包；关闭只影响本项目创建的 Codex 进程，不会修改系统级 Codex 配置。Codex 任务会获得本项目的工作区路径、`tools/bin`、`tools/venv` 和已启用技能目录信息。

`runtime/codex/` 是 Codex CLI 的项目专用 HOME。它不是空模板：首次运行后会包含线程历史、任务状态、日志数据库、配置和 shell 快照；数据库中的表可能为空（例如没有排队任务时 queue 表为 0 行）是正常现象。该目录已加入 `.gitignore`，不会提交到 GitHub。

## 实时事件与模块边界

浏览器统一连接 `GET /api/events` 接收 Codex、附件下载和工具安装事件。事件包含单调递增的 `id`、`type`、`resourceId`、UTC 时间和结构化 `data`；浏览器断线重连会携带 `Last-Event-ID`，服务端在回放窗口内续传缺失事件，超出窗口时发送 `stream.reset` 让页面用当前 API 快照校准。SSE 连接异常不会阻断业务接口，前端会先重连，连续失败后切换为低频轮询，恢复后再停止轮询。

后端正在按清晰边界渐进拆分：`internal/events.py` 负责线程安全事件发布与回放，`internal/routers/realtime.py` 只负责 SSE HTTP 协议；原有 API 路径、请求体和响应格式保持兼容。前端以 `static/app.js` 为渐进迁移入口，`static/modules/api.js`、`state.js`、`dom.js` 和 `realtime.js` 分别承载 API 客户端、状态初始化、故障隔离/全局错误边界和 SSE 连接管理，避免继续把基础设施逻辑堆入单个文件。任一业务模块初始化失败时会在页面顶部指出模块、缺失元素 ID 与原因，并允许单独重试，不阻断其他模块和首屏数据加载。

## 数据目录

所有运行时数据都在项目目录内：

| 路径 | 用途 |
| --- | --- |
| `runtime/config.json` | Web 页面保存的比赛、模型和 Codex 配置，权限为 `0600` |
| `runtime/codex/` | 本项目专用 Codex HOME、会话数据库、日志和技能副本 |
| `runtime/codex/tasks.json` | Codex 任务列表和会话元数据（不含 API Key） |
| `runtime/platform-schema.json` | 最近观测到的平台接口字段集合及变化记录，只包含字段名 |
| `download/` | 题目附件及下载临时文件 |
| `writeups/` | Codex 或手动生成的解题报告 |
| `codex-runs/` | Codex 任务运行记录 |
| `tools/` | 工具、Python 虚拟环境、Go 缓存、pip 缓存和下载临时文件 |
| `tools/dictionaries/` | Agent 自带的授权 CTF 字典；仅用于题目分析，不会写入系统目录 |
| `.venv/` | 工作台自身的 Python 虚拟环境 |
| `temp/tests/` | 测试代码；其他临时内容也应放在 `temp/` |

`runtime/`、下载目录、工具安装目录、缓存、虚拟环境和系统噪声均已加入 `.gitignore`。随项目提供的 `tools/skills/ctf-skills` 技能源和 `tools/dictionaries/` 字典资源会保留在 Git 中；实际安装的工具、缓存和运行数据不会提交。

## 本地 API

API 端点和请求示例见 [`docs/api_doc.md`](docs/api_doc.md)。完成平台绑定并登录后，可以使用辅助脚本提交 Flag：

附件下载采用工作区内的稳定 `.part` 文件和 HTTP Range 请求。网络中断、暂停或程序退出后，再次下载同一附件会从已有字节继续；服务器不支持 Range 时会安全地从头覆盖临时文件，不会把两个响应直接拼接。

需要排查平台接口升级时，可读取 `GET /api/platform/schema` 查看字段诊断。接口只返回字段名、增删变化和时间，不返回 Token、手机号、Flag 或其他响应值。平台认证接口失败会统一返回 `AUTH_EXPIRED`，前端会停止成绩/公告自动刷新并提示重新登录；普通网络错误不会误判为登录过期。

```bash
.venv/bin/python scripts/submit_answer.py 17 'flag{example}'
```

脚本只访问本地服务，不读取平台 Token。非默认监听地址请使用脚本的 `--server` 参数。

## 开发与检查

```bash
PIP_CACHE_DIR="$PWD/tools/cache/pip" .venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check internal main.py scripts temp/tests
.venv/bin/mypy --strict internal main.py scripts
.venv/bin/pytest -q temp/tests
npm ci
npm run check:js
npm run test:e2e
git diff --check
```

Playwright 会启动隔离的本地静态/API/SSE 测试服务，并通过 Chromium 真实点击验证首屏自动加载、题目搜索与组合筛选、Codex 会话树选择和关闭、Escape/背景关闭、Toast 顶层显示、Side 请求正文、警告分级以及 SSE 增量日志/状态更新。测试不读取或修改 `runtime/` 中的真实登录态和任务数据。

提交前请确认暂存区没有 Token、密码、下载产物或运行数据库：

```bash
git status --short
git diff --staged
```

## 安全与边界

- 所有平台请求都由本地后端发起，浏览器不会直接携带登录 Token 请求第三方接口。
- 所有外部输入在 API 层校验，富文本只允许白名单标签，下载路径会进行目录穿越检查。
- 外部 HTTP 调用设置超时；下载和安装任务支持取消，失败不会吞掉异常。
- 本项目不会启动、连接或修改 Docker；题目环境操作仅调用 i春秋平台明确提供的环境接口。
