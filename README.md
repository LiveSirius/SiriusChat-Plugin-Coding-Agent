<h1 align="center">🛠️ Sirius Chat Plugin — Coding Agent</h1>

<div align="center">

<a href="#"><img src="https://img.shields.io/badge/version-2.2.0-blue?style=flat-square" alt="Version 2.2.0"></a>
<a href="#"><img src="https://img.shields.io/badge/Python-3.12+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.12+"></a>
<a href="#"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License MIT"></a>
<a href="#"><img src="https://img.shields.io/badge/Sirius_Chat-Plugin-orange?style=flat-square" alt="Sirius Chat Plugin"></a>

<em>基于 LLM 的 GitHub Issue/PR 全自动管理插件 —— 智能分类、代码审阅、自动修复，一步到位</em>

<br>
<br>
<a href="#-核心特性">✨ 特性</a> ·
<a href="#-快速开始">🚀 快速开始</a> ·
<a href="#-工作流程">💡 流程</a> ·
<a href="#-指令说明">⌨️ 指令</a> ·
<a href="#-模块架构">📦 架构</a> ·
<a href="#-配置说明">⚙️ 配置</a>

</div>

---

## 📋 目录

- [核心特性](#-核心特性)
- [快速开始](#-快速开始)
- [工作流程](#-工作流程)
- [指令说明](#-指令说明)
- [模块架构](#-模块架构)
- [配置说明](#-配置说明)

---

## ✨ 核心特性

### 🎯 **Issue 全生命周期管理**

| 阶段 | 功能 | 说明 |
|------|------|------|
| **检测** | 自动发现 | 通过 Webhook/轮询两种方式捕获新 Issue/PR |
| **过滤** | 垃圾检测 | LLM 自动识别无意义/垃圾提交并人格化关闭 |
| **分类** | 智能标签 | 自动分析 Issue 类型、优先级、难度、模块 |
| **回复** | 人格化回复 | 以角色身份在 Issue 下自动评论 |
| **追问** | 信息收集 | 多轮 LLM 驱动的追问，直至信息充分或判定关闭 |
| **修复** | 自动修复 | 自动 Fork → Clone → 分析 → 修改 → 测试 → PR |
| **审阅** | PR 审阅 | Quick/Deep 双模式代码审阅，支持行内评论 |

### 🤖 **AI Agent 自动修复管线**

```
Issue 就绪 → Fork 仓库 → Clone 代码 → 代码分析 → 工具编辑 → 运行测试 → 创建 PR
```

Agent 使用 4 个内置工具在沙盒环境中工作：

| 工具 | 功能 |
|------|------|
| `search_content` | 关键词搜索，定位相关代码 |
| `read_file_chunk` | 按行读取文件，防止撑爆 Token |
| `search_and_replace_block` | 精确代码替换，校验唯一性防误改 |
| `run_local_test` | 在沙盒中运行测试/检查命令 |

### 🔍 **智能 PR 代码审阅**

- **Quick 模式**：快速概览，给出整体评价
- **Deep 模式**：逐文件行内评论，精准定位问题
- **Incremental 模式**：增量审阅，避免重复评论
- 按维度分类：正确性、安全性、风格、测试、性能
- 三级严重程度：Critical / Warning / Suggestion

### 📺 **实时控制台可视化**

自动修复过程中，弹出独立 CMD 窗口实时显示：

```
============================================================
工作区准备
============================================================
  Fork + Sync + Clone + 创建分支...
============================================================
代码分析
============================================================
  调用工具: search_content(keyword='timeout')
  OK search_content 返回:
     src/main.py:42:...
============================================================
测试验证  (第 1/3 轮)
============================================================
  FAIL pytest -- 测试失败
     AssertionError:...
============================================================
修复完成！
  PR 已创建: https://github.com/owner/repo/pull/42
============================================================
```

### 🔌 **多种接入模式**

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **Webhook** | 由 github_monitor 接收事件后推送 | 有公网 IP/内网穿透 |
| **Polling** | 定时轮询 GitHub API | 无公网 IP，内网环境 |

---

## 🚀 快速开始

### 前置条件

- Sirius Chat v1.1+（插件系统）
- github_monitor SKILL 已配置并运行（管理仓库列表和事件检测）
- 仓库已配置 GitHub Token（读操作用 monitor token，写操作可用独立的 `github_write_token`）

### 安装

**1. 将插件目录放入 Sirius Chat 的插件加载路径：**

```bash
# 插件位于项目的 plugins/ 目录下
# 框架自动从该目录加载
```

**2. 在 WebUI 插件管理页面启用 `coding_agent` 插件：**

- 打开 WebUI → 插件管理
- 找到「编码助手」→ 点击启用
- 配置必要参数（见下方）

**3. 配置插件参数（WebUI 插件设置）：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `github_write_token` | string | `""` | GitHub PAT（fork/PR/标签/评论），留空复用 monitor token |
| `github_username` | string | `""` | GitHub 用户名（git 提交者身份），留空=仓库 owner |
| `github_email` | string | `""` | GitHub 邮箱，留空=`username@users.noreply.github.com` |
| `active_repos` | list | `[]` | 生效仓库（`owner/repo`），留空=monitor 全部 |
| `model` | string | `""` | 自定义 LLM 模型名，留空使用引擎默认模型 |
| `auto_label` | boolean | `true` | Issue 自动标签开关 |
| `auto_review` | boolean | `true` | PR 自动审阅开关 |
| `auto_close_garbage` | boolean | `true` | 自动关闭垃圾 Issue/PR |
| `review_mode` | string | `"quick"` | PR 审阅深度：`quick` / `deep` |
| `max_questions` | int | `12` | 信息收集最大追问次数 |
| `max_retries` | int | `3` | 修复测试最大重试次数 |
| `test_command` | string | `"pytest"` | 测试命令 |
| `lint_command` | string | `""` | 静态检查命令（留空跳过） |
| `console_viewer_enabled` | boolean | `true` | 弹出实时控制台窗口 |
| `console_viewer_keep_open` | boolean | `false` | 修复完成后保持窗口打开 |

**4. 验证插件运行：**

```bash
# 查看插件日志
python main.py persona logs <persona_name> --lines 50
# 日志中应出现类似：
# coding_agent v2.2 启动完成 (monitor_repos=2, effective=2, ...)
```

---

## 💡 工作流程

### Issue 自动处理流程

```
┌─────────────────────────────────────────────────────────┐
│                   1. Issue 打开                           │
│     (Webhook / Polling 检测到新 Issue)                    │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                   2. 垃圾检测                             │
│     LLM 分析 → 若是垃圾 → 人格化关闭评论 + 关闭 Issue    │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                   3. 自动标签                             │
│     LLM 分类: type / priority / difficulty / area        │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                   4. 信息收集（后台自主）                   │
│     ┌─────────────────────────────────┐                  │
│     │ 加载仓库上下文 (README + 结构)    │                  │
│     └─────────────────────────────────┘                  │
│                      │                                   │
│                      ▼                                   │
│     ┌─────────────────────────────────┐                  │
│     │ LLM 分析对话，判断下一步          │                  │
│     └─────────────────────────────────┘                  │
│          │            │           │                      │
│          ▼            ▼           ▼                      │
│      追问用户      信息就绪      判定关闭                  │
│     (AWAITING)    (READY)       (CLOSED)                │
│          │            │                                  │
│          ▼            ▼                                  │
│    等待用户回复   通知管理员                               │
│    ↻ 重新分析     /gh <id> auto                          │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                   5. 自动修复（管理员确认后）              │
│     /gh <task_id> auto → Agent Loop 启动                 │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│               Agent 修复管线                               │
│                                                          │
│  Fork → Sync → Clone → 创建分支                           │
│       ↓                                                  │
│  System Prompt + 工具注册表初始化                          │
│       ↓                                                  │
│  循环: LLM 输出 JSON 工具调用                              │
│    ├─ search_content        ← 关键词搜索定位代码           │
│    ├─ read_file_chunk       ← 读取文件上下文              │
│    ├─ search_and_replace    ← 精确替换代码                │
│    └─ run_local_test        ← 执行测试验证                │
│       ↓                                                  │
│  LLM 输出 {"status":"done"} → 检查 git diff              │
│       ↓                                                  │
│  测试验证 (最多重试 3 次)                                 │
│       ↓                                                  │
│  Git Commit → Push → 创建 Pull Request                   │
│       ↓                                                  │
│  通知管理员 PR 已创建                                     │
└─────────────────────────────────────────────────────────┘
```

### PR 自动审阅流程

```
PR 打开 / Synchronize
       │
       ▼
垃圾检测 ──是──→ 人格化关闭评论 + 关闭 PR
       │
       否
       ▼
已审阅？──是──→ Incremental 模式
       │
       否
       ▼
Quick/Deep 模式
       │
       ▼
LLM 分析 Diff + 变更文件列表
       │
       ▼
输出审阅 JSON（verdict + issues）
       │
       ▼
提交 GitHub Review（行内评论 + 总体评价）
       │
       ▼
私信通知管理员
```

---

## ⌨️ 指令说明

### `/py <code>` — 执行 Python 代码

```bash
/py print('Hello World')
# → Hello World

/py 1+1
# → 2
```

### `/gh <task_id> <action>` — GitHub Agent 指令

**启动自动修复：**

```bash
# task_id 来自 Webhook/Polling 自动创建，或从 /gh status 查询
/gh abc123def456 auto
# → 任务已启动：Issue #42
```

**查询任务状态：**

```bash
/gh abc123def456 status
# → 任务 abc123def456 状态:
#    Issue: #42 - 修复登录超时问题
#    状态: 等待用户回复（已追问 2 次）
#    仓库: owner/repo
#    标签: type:bug priority:high
#    理解: 用户登录时概率性超时
```

**中止任务：**

```bash
/gh abc123def456 abort
# → 任务 abc123def456 已中止
```

**手动触发 PR 审阅：**

```bash
# 单仓库
/gh review 42 quick
# → 已启动 quick 模式审阅 PR #42

# 多仓库（指定索引）
/gh review 0 42 deep
# → 已启动 deep 模式审阅 PR #42（仓库 owner/repo）
```

---

## 📦 模块架构

```
plugins/SiriusChat-Plugin-Coding-Agent/
│
├── main.py              # Plugin 入口 — CodingAgentPlugin（PluginBase 子类）
├── config.py            # 配置模型 (GithubAgentConfig @dataclass)
├── monitor_config.py    # github_monitor 配置读取器
│
├── webhook.py           # Webhook 事件处理器（Issue/PR 业务逻辑层）
├── poller.py            # 轮询模式（替代 Webhook，定时检测新 Issue/PR）
│
├── closer.py            # 垃圾检测与自动关闭（LLM 分析 + 人格化关闭评论）
├── labeler.py           # LLM 自动标签分类（type/priority/difficulty/area）
├── commenter.py         # Issue 智能回复生成
│
├── tracker.py           # Issue 信息队列 & 状态机（后台自主信息收集循环）
├── gatherer.py          # LLM 驱动的信息收集分析（ask/ready/close 三态决策）
│
├── agent_loop.py        # AI Agent 修复管线（工作区→分析→修改→测试→PR）
├── skills.py            # Agent 工具注册表（search/read/edit/test 4 工具）
│
├── review.py            # PR 自动代码审阅（Quick/Deep 模式 + 行内评论）
│
├── api.py               # GitHub REST API 封装（Issue/PR/Label/Fork 等）
│
├── commands.py          # /gh 指令路由与处理
│
├── stream_writer.py     # 实时流写入器（结构化事件日志，供 viewer 消费）
├── console_viewer.py    # 实时控制台可视化窗口（独立 CMD 进程）
│
├── .gitignore
└── README.md
```

### 数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Sirius Chat 引擎                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  CodingAgentPlugin                                            │   │
│  │  ┌──────────┐  ┌───────────┐  ┌────────────┐                 │   │
│  │  │ Webhook  │  │  Poller   │  │  Commands  │                 │   │
│  │  │ Handler  │  │           │  │  (/gh /py) │                 │   │
│  │  └────┬─────┘  └─────┬─────┘  └─────┬──────┘                 │   │
│  │       │              │              │                         │   │
│  │       ▼              ▼              ▼                         │   │
│  │  ┌───────────────────────────────────────┐                    │   │
│  │  │          IssueTracker                 │                    │   │
│  │  │  (后台信息收集 + 状态机)               │                    │   │
│  │  └──────────────┬────────────────────────┘                    │   │
│  │                 │                                              │   │
│  │                 ▼                                              │   │
│  │  ┌───────────────────────────────────────┐                    │   │
│  │  │          Agent Loop                   │                    │   │
│  │  │  (工具调用 + 代码编辑 + 测试 + PR)     │                    │   │
│  │  └───────────────────────────────────────┘                    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  EngineProxy (LLM 调用层 — 人格注入 + 模型路由)               │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  GitHub API (github_monitor event_bridge / GitHubClient)     │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ 配置说明

### 依赖

```python
_plugin_dependencies = ["httpx", "GitPython"]
```

插件自动管理 pip 依赖，启用时自动安装。

### 与 github_monitor SKILL 的集成

插件**不直接管理仓库列表**，而是通过 `monitor_config.py` 自动读取 `github_monitor` SKILL 的配置：

```
{work_path}/skill_data/github_monitor.json
```

这意味着：
- 仓库在 **WebUI → SKILL 设置 → github_monitor** 中统一管理
- 插件通过 `event_bridge` 接收事件通知
- 可通过 `active_repos` 参数过滤生效仓库

### 自动标签规范

| 维度 | 标签 |
|------|------|
| 类型 | `type:bug` · `type:feature` · `type:docs` · `type:question` · `type:refactor` |
| 优先级 | `priority:critical` · `priority:high` · `priority:medium` · `priority:low` |
| 难度 | `difficulty:easy` · `difficulty:medium` · `difficulty:hard` |
| 模块 | `area:core` · `area:api` · `area:ui` · `area:docs` · `area:tests` · `area:config` |
| 状态 | `status:needs-triage` · `status:good-first-issue` · `status:help-wanted` |

### Token 优先级

```
写操作 Token 优先级:
  1. github_write_token（插件配置）
  2. github_monitor per-repo token
  3. 空（不执行写操作）

读操作 Token 优先级:
  1. github_monitor per-repo token
  2. 空（仅限公开仓库）
```

---

## 📝 许可证

MIT License © 2025 Sparrived

---

<div align="center">

**由 [Sirius Chat](https://github.com/Sparrived/SiriusChat) 驱动 · 基于 LLM 的智能代码管理插件**

⭐ 如果你觉得这个插件有帮助，欢迎给 Sirius Chat 项目点个 Star！

</div>
