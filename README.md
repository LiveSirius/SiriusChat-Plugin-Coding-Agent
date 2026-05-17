# SiriusChat Plugin - Coding Agent

> GitHub Issue/PR 自动化管理 + AI 代码修复的 SiriusChat 插件

[![Version](https://img.shields.io/badge/version-2.2.0-blue.svg)](https://github.com/Sparrived/SiriusChat-Plugin-Coding-Agent)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

## 简介

Coding Agent 是 [SiriusChat](https://github.com/Sparrived/SiriusChat) 的一个插件，为你的 GitHub 仓库提供 **AI 驱动的 Issue/PR 全生命周期管理**。它能自动接收 Issue、分析问题、与提问者对话收集信息、修改代码、运行测试、提交 PR，还能自动审阅他人的 PR 并给出专业反馈。

简单来说：**这是一个住在你 GitHub 里的 AI 工程师**。

## 功能特性

### Issue 自动化管理

- **智能标签分类** — 新 Issue 提交后，LLM 自动判断类型（bug/feature/docs/question/refactor）、优先级（critical/high/medium/low）、难度（easy/medium/hard）和模块区域（core/api/ui/docs/tests/config），并打上对应标签
- **垃圾检测与自动关闭** — 识别广告、占位符、无意义提交等垃圾 Issue，生成人格化关闭评论后自动关闭
- **后台信息收集** — 自动拉取仓库 README 和目录结构作为上下文，必要时向提问者追问补充信息（最多 12 轮），直到信息充分
- **对话状态机** — 每个 Issue 维护独立的生命周期状态：`GATHERING_INFO` → `AWAITING_RESPONSE` → `READY` → `FIXING` → `DONE`
- **人格化交互** — 所有 AI 生成的评论都支持 persona 注入，让 AI 以角色身份与用户沟通

### AI 代码修复

- **完整的 Agent 循环** — 管理员审批后，自动执行完整修复流程：
  1. Fork 仓库 → 同步上游 → Clone 到本地 → 创建修复分支
  2. LLM 分析代码定位问题（内置 4 个工具辅助）
  3. 修改代码 → 静态检查（flake8）→ 单元测试（pytest）
  4. 失败自动重试（最多 3 轮）
  5. 测试通过后提交代码 + 创建 PR（附 LLM 生成的 Changelog）
- **内置工具系统** — 4 个 Agent 工具：
  - `search_content` — 使用 ripgrep 搜索关键词
  - `read_file_chunk` — 按行读取文件片段，防止超大文件撑爆 Token
  - `search_and_replace_block` — 精确代码块替换（校验唯一性）
  - `run_local_test` — 在沙盒中运行测试命令
- **实时可视化** — Windows 控制台窗口实时展示 Agent 工作过程（工具调用、测试结果、重试信息等）

### PR 自动审阅

- **自动代码审阅** — 新 PR 提交后自动获取 diff，LLM 按维度（正确性/安全性/风格/测试/性能）分析代码问题
- **结构化审阅报告** — 输出包含严重程度（critical/warning/suggestion）、涉及文件、行号、问题描述和修改建议
- **双模式审阅** — `quick` 模式（快速概览）和 `deep` 模式（深度分析）
- **自动避免重复** — `synchronize` 事件时检查是否已有审阅，避免重复提交

### 聊天命令

- `/py <code>` — 在聊天中直接执行一行 Python 代码并返回结果
- `/gh <task_id> auto` — 启动 Issue 自动修复
- `/gh <task_id> status` — 查询任务状态
- `/gh <task_id> abort` — 中止任务
- `/gh review <pr_number> [quick|deep]` — 手动触发 PR 审阅

## 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    SiriusChat 平台                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐  │
│  │  NapCat  │◄──►│  WebUI   │◄──►│  github_monitor  │  │
│  │ (消息桥) │    │ (配置)   │    │   (事件检测)     │  │
│  └────┬─────┘    └────┬─────┘    └────────┬─────────┘  │
│       │               │                   │            │
│       ▼               ▼                   ▼            │
│  ┌─────────────────────────────────────────────────┐    │
│  │           Coding Agent Plugin (本项目)           │    │
│  │                                                 │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │    │
│  │  │ webhook  │  │ labeler  │  │    closer     │  │    │
│  │  │ (事件处理)│  │ (自动标签)│  │ (垃圾关闭)   │  │    │
│  │  └────┬─────┘  └──────────┘  └──────────────┘  │    │
│  │       │                                         │    │
│  │       ▼                                         │    │
│  │  ┌──────────────────────────────────────────┐   │    │
│  │  │            IssueTracker                  │   │    │
│  │  │  (状态机 + 后台信息收集循环)              │   │    │
│  │  │                                          │   │    │
│  │  │  GATHERING_INFO → AWAITING_RESPONSE      │   │    │
│  │  │       ↓              ↓                   │   │    │
│  │  │     READY  ←────────┘                   │   │    │
│  │  │       ↓                                 │   │    │
│  │  │     FIXING → DONE                       │   │    │
│  │  └──────────────┬───────────────────────────┘   │    │
│  │                 │                               │    │
│  │                 ▼                               │    │
│  │  ┌──────────────────────────────────────────┐   │    │
│  │  │           Agent Loop                     │   │    │
│  │  │                                          │   │    │
│  │  │  Fork → Clone → LLM + Tools → Test → PR │   │    │
│  │  │                                          │   │    │
│  │  │  Tools: search / read / replace / test   │   │    │
│  │  └──────────────────────────────────────────┘   │    │
│  │                                                 │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │    │
│  │  │ reviewer │  │commenter │  │  gatherer    │  │    │
│  │  │ (PR审阅) │  │ (智能回复)│  │ (信息收集)   │  │    │
│  │  └──────────┘  └──────────┘  └──────────────┘  │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │              GitHub API (REST)                   │    │
│  │  Issues / PRs / Labels / Reviews / Forks / ...  │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## 安装

### 前置要求

- Python 3.11+
- [SiriusChat](https://github.com/Sparrived/SiriusChat) 已安装并运行
- GitHub Personal Access Token (PAT)
- `ripgrep` (用于代码搜索工具)

### 安装步骤

```bash
# 1. 克隆仓库到 SiriusChat 的插件目录
cd /path/to/SiriusChat/plugins
git clone https://github.com/Sparrived/SiriusChat-Plugin-Coding-Agent.git

# 2. 安装依赖
pip install httpx GitPython

# 3. 在 SiriusChat WebUI 中配置插件（见下方配置说明）
```

## 配置

在 SiriusChat WebUI 的插件设置中配置以下参数：

### 基础配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `github_write_token` | string | `""` | GitHub PAT（用于 Fork/PR/标签/评论等写操作），留空则复用 monitor token |
| `github_username` | string | `""` | GitHub 用户名（git 提交者身份），留空默认为仓库 owner |
| `github_email` | string | `""` | GitHub 邮箱（git 提交者 email），留空默认为 `username@users.noreply.github.com` |
| `active_repos` | list | `[]` | 生效仓库列表（`owner/repo` 格式），留空则作用于 monitor 配置的全部仓库 |

### Agent 配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | string | `""` | 自定义 LLM 模型名，留空使用系统默认模型 |
| `max_retries` | int | `3` | 代码修复最大重试轮数 |
| `max_questions` | int | `12` | 信息收集最大追问次数 |
| `test_command` | string | `"pytest"` | 测试命令（修复完成后自动执行） |

### 功能开关

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auto_label` | boolean | `true` | 启用 Issue 自动标签分类 |
| `auto_review` | boolean | `true` | 启用 PR 自动代码审阅 |
| `auto_close_garbage` | boolean | `true` | 自动关闭垃圾 Issue/PR |
| `review_mode` | string | `"quick"` | PR 审阅深度：`quick`（快速概览）或 `deep`（深度分析） |

### 控制台可视化

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `console_viewer_enabled` | boolean | `true` | 代码修复时弹出实时控制台窗口 |
| `console_viewer_keep_open` | boolean | `false` | 修复完成后保持窗口打开 |

## 工作流程

### Issue 生命周期

```
新 Issue 提交
    │
    ▼
┌─────────────┐
│  垃圾检测   │──是──▶ 生成关闭评论 → 关闭 Issue
└─────┬───────┘
      │否
      ▼
┌─────────────┐
│  自动标签   │──▶ 根据内容自动分类打标签
└─────┬───────┘
      │
      ▼
┌─────────────────┐
│  后台信息收集    │◀──────────────────┐
│  (IssueTracker)  │                   │
└─────┬───────────┘                   │
      │                               │
      ├── 信息不足 ──▶ 追问提问者 ──▶ 等待回复
      │                               │
      ├── 需要查看代码 ──▶ 获取文件 ──┘
      │
      ├── 应关闭 ──▶ 生成关闭评论 → 关闭 Issue
      │
      └── 信息就绪 ──▶ 通知管理员
                        │
                        ▼
                  ┌─────────────┐
                  │  管理员审批  │──▶ /gh <task_id> auto
                  └─────┬───────┘
                        │
                        ▼
                  ┌─────────────┐
                  │  Agent Loop │
                  └─────┬───────┘
                        │
                        ▼
                  ┌─────────────┐
                  │  创建 PR    │──▶ 完成
                  └─────────────┘
```

### Agent Loop 详解

```
1. 工作区准备
   ├── Fork 仓库（幂等）
   ├── Sync upstream
   ├── Clone 到本地
   └── 创建 fix-issue-{number} 分支

2. 代码分析与修改
   ├── LLM 接收 Issue 信息 + 工具定义
   ├── 循环调用工具（最多 15 轮）：
   │   ├── search_content — 定位相关代码
   │   ├── read_file_chunk — 查看上下文
   │   ├── search_and_replace_block — 修改代码
   │   └── run_local_test — 运行测试
   └── LLM 输出 {"status": "done"} 结束循环

3. 验证阶段（最多 3 轮重试）
   ├── flake8 . — 静态检查
   ├── pytest — 单元测试
   ├── 失败 → LLM 分析错误并修复 → 重新验证
   └── 全部通过 → 进入提交阶段

4. 提交与 PR
   ├── git add + commit（自动生成 commit message）
   ├── git push origin fix-issue-{number}
   ├── LLM 生成 Changelog
   └── 创建 PR（标题: Fix #{number}: {title}）
```

## 文件结构

```
SiriusChat-Plugin-Coding-Agent/
├── main.py              # 插件入口，注册事件处理器和命令
├── config.py            # 插件配置模型 (GithubAgentConfig)
├── api.py               # GitHub API 封装 (Issue/PR/Label/Fork/...)
├── agent_loop.py        # Agent 核心循环（工作区 → 分析 → 修改 → 测试 → PR）
├── tracker.py           # Issue 状态机 & 后台信息收集循环
├── webhook.py           # Webhook 事件处理器（Issue/PR/Comment）
├── commands.py          # /gh 指令处理（auto/status/abort/review）
├── labeler.py           # Issue 自动标签分类
├── review.py            # PR 自动代码审阅
├── closer.py            # 垃圾 Issue/PR 检测与自动关闭
├── gatherer.py          # LLM 驱动的信息收集与分析
├── commenter.py         # Issue 智能回复评论生成
├── skills.py            # Agent 工具注册表（4 个内置工具）
├── stream_writer.py     # 结构化事件日志（供 console_viewer 消费）
├── console_viewer.py    # Windows 控制台实时可视化
└── monitor_config.py    # 从 github_monitor 读取仓库配置
```

## 依赖

- `httpx` — 异步 HTTP 客户端（GitHub API 调用）
- `GitPython` — Git 操作（clone/branch/commit/push）
- `ripgrep` — 代码搜索（`search_content` 工具依赖）

## 开发

### 本地开发

```bash
# 克隆仓库
git clone https://github.com/Sparrived/SiriusChat-Plugin-Coding-Agent.git
cd SiriusChat-Plugin-Coding-Agent

# 安装依赖
pip install httpx GitPython

# 启动 SiriusChat 并加载插件
```

### 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 许可证

本项目采用 MIT 许可证 — 详见 [LICENSE](LICENSE) 文件。

## 致谢

- [SiriusChat](https://github.com/Sparrived/SiriusChat) — 提供插件框架和 GitHub 事件桥接
- 所有贡献者和使用者

---

> **注意**：本插件需要配合 SiriusChat 的 `github_monitor` 技能使用，仓库列表和事件检测由 `github_monitor` 统一管理。

