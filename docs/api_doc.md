# 本地 Web API 文档

本文档描述 Python 工作台提供的本地 HTTP API。服务负责保存 i春秋登录态并访问平台，调用方无需接触平台 Token 或请求签名。

## 公共约定

默认 Base URL：

```text
http://127.0.0.1:8080
```

JSON 接口成功时返回：

```json
{
  "ok": true,
  "data": {}
}
```

失败时返回：

```json
{
  "ok": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "可读错误信息"
  }
}
```

除验证码图片外，响应均使用 JSON。配置、登录和数据响应带有 `Cache-Control: no-store`。带 `refresh=true` 的查询会跳过本地缓存。

平台绑定与登录写操作必须携带：

```text
X-GCSIS-Action: platform-auth
```

批量下载、清空和重新下载附件的写操作必须携带：

```text
X-GCSIS-Action: attachment-manager
```

工具安装操作必须携带：

```text
X-GCSIS-Action: tools-manager
```

## 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/config` | 查询脱敏后的页面配置 |
| `PUT` | `/api/config` | 保存模型和 Codex 配置 |
| `POST` | `/api/platform/bind` | 绑定 i春秋比赛 |
| `GET` | `/api/platform/auth` | 查询比赛登录态 |
| `GET` | `/api/platform/captcha` | 获取人工处理的验证码图片 |
| `POST` | `/api/platform/login/password` | 密码登录 |
| `POST` | `/api/platform/login/sms/send` | 发送短信验证码 |
| `POST` | `/api/platform/login/sms` | 短信验证码登录 |
| `GET` | `/api/platform/schema` | 查询最近观测到的平台接口字段变化（仅字段名） |
| `GET` | `/api/match-info` | 查询比赛信息 |
| `GET` | `/api/overview` | 查询队伍得分与排名 |
| `GET` | `/api/exercises` | 查询题目列表 |
| `GET` | `/api/exercises/{id}` | 查询题目详情 |
| `POST` | `/api/exercises/{id}/answer` | 提交 Flag |
| `POST` | `/api/exercises/{id}/environment/start` | 启动题目环境 |
| `POST` | `/api/exercises/{id}/environment/stop` | 回收题目环境 |
| `GET` | `/api/notices` | 查询公告列表 |
| `GET` | `/api/notices/{id}` | 查询公告详情 |
| `GET` | `/api/attachments` | 查询本地附件目录 |
| `POST` | `/api/exercises/{id}/attachments/{index}/download` | 下载一个附件 |
| `POST` | `/api/exercises/{id}/attachments/download-all` | 下载一道题的全部附件 |
| `DELETE` | `/api/exercises/{id}/attachments` | 清空一道题的本地附件 |
| `POST` | `/api/exercises/{id}/attachments/redownload` | 重新下载一道题的附件 |
| `POST` | `/api/attachments/download-all` | 下载全部题目附件 |
| `POST` | `/api/attachments/downloads` | 启动带进度的附件下载任务 |
| `GET` | `/api/attachments/downloads` | 查询当前附件下载任务 |
| `GET` | `/api/attachments/downloads/{task_id}` | 查询附件下载任务进度 |
| `POST` | `/api/attachments/sizes` | 启动附件大小探测 |
| `GET` | `/api/attachments/sizes` | 查询附件大小探测进度 |
| `GET` | `/api/tools` | 查询本地 CTF 工具目录和安装状态 |
| `POST` | `/api/tools` | 在当前目录安装选中的 CTF 工具 |
| `POST` | `/api/tools/cancel` | 取消正在进行的工具安装 |
| `DELETE` | `/api/tools/{tool_id}` | 卸载指定的本地 CTF 工具 |
| `POST` | `/api/tools/{tool_id}/uninstall` | 卸载指定的本地 CTF 工具（兼容写法） |
| `GET` | `/api/environment` | 查询本机常用开发环境状态 |
| `GET` | `/api/exercises/{id}/ai/prompt` | 生成 AI 解题提示词 |
| `POST` | `/api/exercises/{id}/ai/run` | 使用兼容模型解题 |
| `POST` | `/api/exercises/{id}/codex/run` | 启动 Codex 解题任务 |
| `POST` | `/api/exercises/{id}/codex/pure` | 启动 Codex 纯解题任务 |
| `GET` | `/api/codex/tasks` | 查询 Codex 任务列表 |
| `GET` | `/api/codex/tasks/{task_id}` | 查询 Codex 任务详情 |
| `POST` | `/api/codex/tasks/{task_id}/message` | 在当前会话继续输入或创建 Side 对话 |
| `POST` | `/api/codex/tasks/{task_id}/continue` | 发送标准故障恢复提示并继续任务 |
| `POST` | `/api/codex/tasks/{task_id}/cancel` | 取消 Codex 任务 |
| `DELETE` | `/api/codex/tasks/{task_id}` | 删除已结束任务及本地日志 |

## 页面配置

### 查询配置

`GET /api/config`

```json
{
  "ok": true,
  "data": {
    "configured": true,
    "matchUrl": "https://match.ichunqiu.com/index",
    "matchKeyConfigured": true,
    "platformTokenConfigured": true,
    "modelBaseUrl": "https://model.example.test/v1",
    "modelName": "solver-model",
    "modelApiKeyConfigured": true,
    "codexBaseUrl": "https://codex.example.test/v1",
    "codexModel": "codex-model",
    "codexApiKeyConfigured": true,
    "codexMaxConcurrency": 5,
    "codexCtfSkillsEnabled": true
  }
}
```

`matchUrl` 不包含查询参数。平台 Token、比赛规范 key 和完整的模型 API Key 均不会返回；如果已配置模型 Key，响应会提供不可逆的脱敏字段供页面识别（例如 `sk-b…g9J9`）。

### 保存模型配置

`PUT /api/config`

```json
{
  "modelBaseUrl": "https://model.example.test/v1",
  "modelName": "solver-model",
  "modelApiKey": "example-model-key",
  "codexBaseUrl": "https://codex.example.test/v1",
  "codexModel": "codex-model",
  "codexApiKey": "example-codex-key",
  "codexMaxConcurrency": 5
}
```

字段可以省略；省略的敏感字段保持原值。传入空字符串会清空对应值。`codexMaxConcurrency` 允许 `1` 到 `16`，默认值为 `5`；超过并发上限的 Codex 任务会进入队列，正在运行的任务不会被暂停或取消。`codexCtfSkillsEnabled` 默认为 `true`，用于启用或关闭项目内置 CTF 技能包。比赛地址和登录态必须使用平台接口修改，不能通过此接口写入。

## 比赛绑定与登录

### 绑定比赛

`POST /api/platform/bind`

Header：`X-GCSIS-Action: platform-auth`

```json
{
  "matchUrl": "https://match.ichunqiu.com/wanqubei",
  "platformToken": "optional-existing-token"
}
```

也支持带入口参数的地址：

```text
https://match.ichunqiu.com/index?k=example_entry_key
```

`platformToken` 可省略或留空。此时服务向平台申请匿名 Token，绑定完成后再通过密码或短信登录。响应包含 `authenticated`、`matchTitle` 和脱敏后的配置状态，不返回 Token 或入口参数。

### 查询登录态

`GET /api/platform/auth`

```json
{
  "ok": true,
  "data": {
    "authenticated": true,
    "teamName": "队伍名称",
    "message": ""
  }
}
```

### 获取验证码

`GET /api/platform/captcha?kind=image`

`kind` 取值：

- `image`：密码登录需要的四位图形验证码。
- `rotate`：短信登录需要人工旋转的图片。

成功响应为 `image/*`。验证码只提供给页面显示，不做自动识别。

### 密码登录

`POST /api/platform/login/password`

Header：`X-GCSIS-Action: platform-auth`

```json
{
  "account": "example-account",
  "password": "example-only",
  "imageCode": "A1B2"
}
```

首次请求可以省略 `imageCode`。平台返回错误码 `1002` 时，获取 `kind=image` 的验证码并由用户手动输入后重试。账号、密码和验证码不会持久化。

### 短信登录

先人工旋转 `kind=rotate` 的验证码图片，再发送短信：

`POST /api/platform/login/sms/send`

Header：`X-GCSIS-Action: platform-auth`

```json
{
  "areaId": "86",
  "phone": "13800000000",
  "rotation": 127
}
```

`rotation` 为 `0` 到 `360` 的整数角度。收到短信后登录：

`POST /api/platform/login/sms`

Header：`X-GCSIS-Action: platform-auth`

```json
{
  "phone": "13800000000",
  "smsCode": "123456"
}
```

## 比赛数据

### 比赛信息

`GET /api/match-info?refresh=true`

```json
{
  "ok": true,
  "data": {
    "note": "比赛名称和时间",
    "rule": "比赛状态、参赛方式和登录方式"
  }
}
```

### 得分与排名

`GET /api/overview?refresh=true`

```json
{
  "ok": true,
  "data": {
    "stagePoint": 88.5,
    "stageRank": 7
  }
}
```

### 题目列表

`GET /api/exercises?refresh=true`

```json
{
  "ok": true,
  "data": [
    {
      "id": 1,
      "name": "Web",
      "order": 1,
      "corpus": [
        {
          "id": 17,
          "name": "easy-web",
          "order": 1,
          "isOpen": true,
          "hasSolved": false
        }
      ]
    }
  ]
}
```

后续题目、附件、环境和 Flag 接口必须使用这里返回的本地题目 `id`，不能使用平台原始 ID。

### 题目详情

`GET /api/exercises/{id}?refresh=true`

```json
{
  "ok": true,
  "data": {
    "id": 17,
    "name": "easy-web",
    "description": "题目描述",
    "hasSolved": false,
    "score": "100",
    "difficulty": "2",
    "attachment": {
      "files": [
        {
          "name": "attachment.zip",
          "url": "https://files.example.test/attachment.zip",
          "ext": "zip"
        }
      ]
    },
    "endpoints": [],
    "canRefreshEndpoint": false,
    "isNeedInit": true,
    "endpointType": "docker",
    "currentTime": 1780000000000,
    "isNeedCheck": false
  }
}
```

`isNeedInit=true` 表示需要启动环境；`isNeedCheck=true` 表示环境仍在准备中，应稍后刷新详情。环境就绪后，`endpoints` 包含地址、端口、账号和过期时间。

### 提交 Flag

`POST /api/exercises/{id}/answer`

```json
{
  "flag": "answer"
}
```

只提交花括号内部内容，例如 `flag{answer}` 应提交为 `answer`。成功响应：

```json
{
  "ok": true,
  "data": {
    "isCorrect": true
  }
}
```

命令行辅助脚本同样调用此本地接口：

```bash
.venv/bin/python scripts/submit_answer.py 17 'flag{answer}'
```

### 环境操作

```text
POST /api/exercises/{id}/environment/start
POST /api/exercises/{id}/environment/stop
```

启动成功返回 `{"accepted": true}`。随后轮询题目详情，直到 `isNeedCheck=false`。完成题目后应调用停止接口回收环境。

### 公告

```text
GET /api/notices?refresh=true
GET /api/notices/{id}?refresh=true
```

公告列表返回 `id`、`title`、`content`、`createdAt`、`createdTime` 和 `userName`。详情额外返回 `isFile`、`file` 和 `url`。

## 附件操作

查询本地附件状态：

```text
GET /api/attachments
```

下载单个附件不需要动作 Header：

```text
POST /api/exercises/{id}/attachments/{index}/download
```

以下本地批量或删除操作必须带 `X-GCSIS-Action: attachment-manager`，且请求体必须为空：

```text
POST   /api/attachments/download-all
POST   /api/exercises/{id}/attachments/download-all
DELETE /api/exercises/{id}/attachments
POST   /api/exercises/{id}/attachments/redownload
```

附件只会保存到当前工作目录的 `download/` 下。

页面使用后台任务接口执行下载。启动全部缺失附件下载：

```http
POST /api/attachments/downloads
X-GCSIS-Action: attachment-manager
Content-Type: application/json

{"scope":"all"}
```

`scope` 还支持 `category`、`exercise`、`single` 和 `redownload`。`category` 需要 `category` 字段；`exercise` 和 `redownload` 需要 `exerciseId`，`single` 还需要从零开始的 `attachmentIndex`。接口返回 `202` 和任务 ID，随后查询：

```text
GET /api/attachments/downloads/{task_id}
```

单个附件和批量附件使用相同的后台任务与控制接口。暂停、继续或取消任务：

```text
POST /api/attachments/downloads/{task_id}/pause
POST /api/attachments/downloads/{task_id}/resume
POST /api/attachments/downloads/{task_id}/cancel
```

控制接口必须带 `X-GCSIS-Action: attachment-manager` 且请求体为空。取消会删除当前附件的未完成临时分片，已经完整下载的附件会保留。进度状态可能为 `running`、`paused`、`cancelling`、`cancelled`、`completed`、`completed_with_errors` 或 `failed`。

进度数据包含 `totalFiles`、`completedFiles`、`currentBytes`、`currentTotalBytes`、`speedBytesPerSecond` 和 `etaSeconds`。附件大小探测使用：

```text
POST /api/attachments/sizes
GET  /api/attachments/sizes
```

在 POST 地址后添加 `?refresh=true` 会重新探测。探测结果缓存在当前进程内，并通过题目详情附件的 `size` 字段和附件目录返回。

按类别下载时，请使用题目列表中显示的类别名称；没有类别的题目归入 `未分类`：

```http
POST /api/attachments/downloads
X-GCSIS-Action: attachment-manager
Content-Type: application/json

{"scope":"category","category":"Web"}
```

类别名称旁会显示题目数、附件数和已知总大小，页面可以折叠类别并一键下载该类别。附件大小在登录后自动探测，不需要页面上的手动探测按钮。

## CTF 工具

查询工具状态：

```text
GET /api/tools
```

启动安装任务：

```http
POST /api/tools
X-GCSIS-Action: tools-manager
Content-Type: application/json

{"toolIds":["sqlmap","z3"],"all":false}
```

一键安装全部工具时使用 `{"toolIds":[],"all":true}`。安装状态会通过 `GET /api/tools` 轮询，所有文件保存在当前目录的 `tools/`。

`GET /api/tools` 返回的每个工具包含 `category` 和 `categoryLabel`：

- `python-library` / `Python 库`：安装到 `tools/venv`。
- `go-project` / `Go 开源项目`：使用重定向到 `tools/go` 的 Go 缓存编译，入口位于 `tools/bin`。
- `binary-package` / `独立工具 / 发行包`：下载并解压到 `tools/`。

响应中的 `progress` 包含 `currentBytes`、`currentTotal`、`speed`、`eta`、`phase` 和 `message`。上游没有提供总大小时，`indeterminate` 为 `true`，此时 `eta` 为 0，但速度仍会根据实际下载或本地缓存增长计算。

卸载工具：

```http
DELETE /api/tools/sqlmap
X-GCSIS-Action: tools-manager
```

卸载只处理当前工作目录的 `tools/` 内容。Python 工具通过 `tools/venv/bin/pip` 移除，Go 工具、发行包和启动器只删除对应的本地文件；不会调用系统包管理器，也不会删除工作台 `.venv`、`tools/skills/ctf-skills` 或系统级运行时。安装任务进行时，卸载请求会返回 `409`。

本地环境检测使用 `GET /api/environment`，页面只在工具管理页展示。每项包含 `label`、`available`、`version` 和可选的 `path`，检测过程不会安装或修改系统运行时。

## AI 与 Codex

OpenAI 兼容模型和 Codex 参数在页面中分别配置。前者由工作台请求 Chat Completions 接口；后者启动本机 Codex CLI，通过 Responses 接口执行带日志和任务状态的完整流程。服务同时支持两种协议时可以填写相同的地址、模型和 API Key，但配置相互独立。未配置相应能力时，接口返回 `503`。

普通 AI 解题：

```text
GET  /api/exercises/{id}/ai/prompt
POST /api/exercises/{id}/ai/run
```

Codex 后台任务：

```text
POST /api/exercises/{id}/codex/run
POST /api/exercises/{id}/codex/pure
GET  /api/codex/tasks
GET  /api/codex/tasks/{task_id}
POST /api/codex/tasks/{task_id}/cancel
POST /api/codex/tasks/{task_id}/continue
DELETE /api/codex/tasks/{task_id}
```

`GET /api/codex/tasks` 即使当前 Codex CLI 暂不可用也会返回已经持久化的任务，并通过 `available: false` 标识不可启动新任务。任务元数据保存在 `runtime/codex/tasks.json`，不包含 API Key。

附件下载使用工作区内的 `.part` 临时文件和 HTTP `Range` 请求。下载中断后重新启动同一附件会从已有字节继续；服务器返回 `200` 表示不支持 Range 时，服务会覆盖临时文件重新下载，避免内容重复拼接。

`codex/run` 允许通过本地 API 操作题目环境和提交答案；`codex/pure` 只分析已有题目说明与本地附件，不访问比赛接口、不启动环境、不提交 Flag。

任务详情页的“再次输入”会在原会话中继续，“Side 提问”会 fork 一个独立会话并标记为 Side，不影响主任务。两种操作都由本地 Codex CLI 的 `resume` / `fork` 子命令执行。

消息接口请求体为 `{"message":"补充要求","side":false}`；将 `side` 设为 `true` 会创建独立的 Side 会话。

Codex 任务会自动使用后端生成的标准题目 AI 提示词作为任务正文；启动窗口中可编辑的内容是本次任务附加的系统指令，两者都会发送给 Codex。
