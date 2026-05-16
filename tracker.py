"""Issue 信息队列 & 状态机。

每个活跃 Issue 维护一份 IssueState，通过 DataStore 持久化。
后台轮询检查状态并驱动信息收集循环。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .api import get_file_content, get_issue_comments
from .gatherer import analyze_and_gather

logger = logging.getLogger(__name__)

_TRACKER_TICK = 60
_PREFIX = "tracker_"


class IssueState:
    __slots__ = (
        "issue_number", "repo", "title", "body", "labels",
        "status", "conversation", "gathered_summary", "last_comment_fetched_at",
        "questions_asked", "last_activity", "task_id",
    )

    def __init__(
        self,
        issue_number: int,
        repo: str,
        title: str = "",
        body: str = "",
        labels: list[str] | None = None,
        status: str = "GATHERING_INFO",
        conversation: list[dict] | None = None,
        gathered_summary: str = "",
        last_comment_fetched_at: float = 0.0,
        questions_asked: int = 0,
        last_activity: float | None = None,
        task_id: str = "",
    ) -> None:
        self.issue_number = issue_number
        self.repo = repo
        self.title = title
        self.body = body
        self.labels = labels or []
        self.status = status
        self.conversation = conversation or []
        self.gathered_summary = gathered_summary
        self.last_comment_fetched_at = last_comment_fetched_at
        self.questions_asked = questions_asked
        self.last_activity = last_activity or time.time()
        self.task_id = task_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_number": self.issue_number,
            "repo": self.repo,
            "title": self.title,
            "body": self.body,
            "labels": self.labels,
            "status": self.status,
            "conversation": self.conversation,
            "gathered_summary": self.gathered_summary,
            "last_comment_fetched_at": self.last_comment_fetched_at,
            "questions_asked": self.questions_asked,
            "last_activity": self.last_activity,
            "task_id": self.task_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IssueState:
        return cls(
            issue_number=int(data.get("issue_number", 0)),
            repo=str(data.get("repo", "")),
            title=str(data.get("title", "")),
            body=str(data.get("body", "")),
            labels=list(data.get("labels", [])),
            status=str(data.get("status", "GATHERING_INFO")),
            conversation=list(data.get("conversation", [])),
            gathered_summary=str(data.get("gathered_summary", "")),
            last_comment_fetched_at=float(data.get("last_comment_fetched_at", 0)),
            questions_asked=int(data.get("questions_asked", 0)),
            last_activity=float(data.get("last_activity", 0)) or time.time(),
            task_id=str(data.get("task_id", "")),
        )

    def add_conversation(self, role: str, content: str) -> None:
        self.conversation.append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
        self.last_activity = time.time()


class IssueTracker:
    """维护所有活跃 Issue 的状态，驱动信息收集循环。"""

    def __init__(
        self,
        data_store: Any,
        config: dict[str, Any],
        engine_proxy: Any,
        plugin_ctx: Any,
    ) -> None:
        self._store = data_store
        self._config = config
        self._engine_proxy = engine_proxy
        self._plugin_ctx = plugin_ctx
        self._task: asyncio.Task | None = None
        self._running = False
        self._engine_unbound_logged = False

    def _engine_ready(self) -> bool:
        engine = getattr(self._engine_proxy, "get_engine", lambda: None)()
        return engine is not None

    @property
    def _adapter(self) -> Any:
        """动态获取 adapter（NapCat 连接后才注入到 ctx）。"""
        return getattr(self._plugin_ctx, "adapter", None)

    def enqueue(self, issue_number: int, repo: str, title: str, body: str, labels: list[str]) -> str:
        import uuid
        task_id = uuid.uuid4().hex[:12]
        state = IssueState(
            issue_number=issue_number, repo=repo, title=title, body=body,
            labels=labels, task_id=task_id,
        )
        state.add_conversation("user", f"Issue #{issue_number}: {title}\n\n{body}")
        self._store.set(f"{_PREFIX}{task_id}", state.to_dict())
        logger.info("Tracker: Issue #%d (%s) 入队, task_id=%s, status=%s, labels=%s",
                     issue_number, repo, task_id, state.status, state.labels)
        return task_id

    def get_state(self, task_id: str) -> IssueState | None:
        raw = self._store.get(f"{_PREFIX}{task_id}")
        if raw is None:
            return None
        return IssueState.from_dict(raw if isinstance(raw, dict) else {})

    def _save(self, state: IssueState) -> None:
        self._store.set(f"{_PREFIX}{state.task_id}", state.to_dict())

    def list_active(self) -> list[IssueState]:
        result: list[IssueState] = []
        all_data = self._store.all() if hasattr(self._store, "all") else {}
        for key, raw in all_data.items():
            if not key.startswith(_PREFIX):
                continue
            data = raw if isinstance(raw, dict) else {}
            state = IssueState.from_dict(data)
            if state.status in ("GATHERING_INFO", "AWAITING_RESPONSE", "APPROVED"):
                result.append(state)
        return result

    # ── 后台循环 ──

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        engine_ready = self._engine_ready()
        logger.info("Tracker: 后台循环启动 (间隔%ds, 引擎=%s, adapter=%s, active_repos=%s)",
                     _TRACKER_TICK, "已绑定" if engine_ready else "待绑定",
                     "已注入" if self._adapter else "待注入",
                     self._config.get("active_repos", []))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Tracker: 后台循环已停止")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Tracker: tick 异常")
            await asyncio.sleep(_TRACKER_TICK)

    async def _tick(self) -> None:
        if not self._engine_ready():
            if not self._engine_unbound_logged:
                logger.info("Tracker: 引擎未绑定，等待首个命令触发绑定后开始工作")
                self._engine_unbound_logged = True
            return

        self._engine_unbound_logged = False
        active = self.list_active()
        if active:
            logger.debug("Tracker: tick - %d 个活跃 Issue", len(active))
        for state in active:
            try:
                await self._process(state)
            except Exception:
                logger.exception("Tracker: 处理 Issue #%d 异常", state.issue_number)

    async def _process(self, state: IssueState) -> None:
        logger.debug("Tracker: 处理 Issue #%d status=%s q=%d conv=%d",
                     state.issue_number, state.status, state.questions_asked, len(state.conversation))

        # 1. 拉取新评论
        await self._fetch_new_comments(state)

        # 2. GATHERING_INFO → 分析是否就绪
        if state.status == "GATHERING_INFO":
            await self._try_gather(state)

        # 3. AWAITING_RESPONSE → 等待用户回复后重新分析
        elif state.status == "AWAITING_RESPONSE":
            since_last = time.time() - state.last_activity
            if since_last > 120:
                logger.debug("Tracker: Issue #%d 等待回复超时 %.0fs，重新分析", state.issue_number, since_last)
                await self._try_gather(state)
            else:
                logger.debug("Tracker: Issue #%d 等待回复中 (%.0fs/120s)",
                             state.issue_number, since_last)

    async def _fetch_new_comments(self, state: IssueState) -> None:
        import datetime
        since = None
        if state.last_comment_fetched_at > 0:
            since = datetime.datetime.fromtimestamp(
                state.last_comment_fetched_at, tz=datetime.timezone.utc
            ).isoformat()
        comments = await get_issue_comments(state.repo, state.issue_number, self._config, since=since)
        state.last_comment_fetched_at = time.time()
        had_new = False

        for c in comments:
            user_login = c.get("user", {}).get("login", "unknown")
            body = c.get("body", "")
            if not body:
                continue
            existing_bodies = {m["content"] for m in state.conversation if m["role"] == "assistant"}
            if body in existing_bodies:
                continue
            state.add_conversation("user", f"@{user_login}: {body}")
            had_new = True
            logger.info("Tracker: Issue #%d 新评论 @%s (共%d条对话)",
                         state.issue_number, user_login, len(state.conversation))

        # 有新评论时尝试调整标签
        if had_new and self._config.get("auto_label", True):
            logger.debug("Tracker: Issue #%d 有新评论，尝试调整标签", state.issue_number)
            try:
                from .labeler import adjust_labels_for_issue
                new_labels = await adjust_labels_for_issue(
                    state.issue_number, state.repo, state.title, state.conversation,
                    self._config, self._engine_proxy,
                )
                if new_labels:
                    from .api import add_labels_to_issue
                    await add_labels_to_issue(state.repo, state.issue_number, new_labels, self._config)
                    state.labels = list(set(state.labels) | set(new_labels))
                    logger.info("Tracker: Issue #%d 补充标签 %s", state.issue_number, new_labels)
            except Exception as exc:
                logger.debug("Tracker: Issue #%d 标签调整失败: %s", state.issue_number, exc)

        self._save(state)

    async def _try_gather(self, state: IssueState) -> None:
        max_q = self._config.get("max_questions", 3)
        code_context: dict[str, str] = {}
        fetched_files: set[str] = set()

        logger.info("Tracker: Issue #%d 开始信息收集 (q=%d/%d, conv=%d)",
                     state.issue_number, state.questions_asked, max_q, len(state.conversation))

        # 多轮信息收集：最多 2 轮代码查看 + 1 轮最终决策
        for round_num in range(1, 4):
            result = await analyze_and_gather(state, self._engine_proxy, code_context or None, model=self._config.get("model", ""))

            # 请求查看文件 → 获取后重新分析
            look_at = result.get("look_at_files", [])
            if look_at and round_num < 3:
                new_files = [f for f in look_at if f not in fetched_files]
                if new_files:
                    logger.info("Tracker: Issue #%d 请求查看文件: %s", state.issue_number, new_files)
                    for file_path in new_files[:3]:
                        content = await get_file_content(state.repo, file_path, config=self._config)
                        if content:
                            code_context[file_path] = content
                            fetched_files.add(file_path)
                            logger.info("Tracker: Issue #%d 已获取 %s (%d 字符)",
                                         state.issue_number, file_path, len(content))
                    continue  # 重新分析（带新代码上下文）

            action = result.get("action", "ask")
            logger.info("Tracker: Issue #%d 决策 action=%s round=%d understanding=%s",
                         state.issue_number, action, round_num,
                         result.get("understanding", "")[:80])

            # 关单
            if action == "close":
                close_reason = result.get("close_reason", "经分析此 Issue 无需继续跟进")
                logger.info("Tracker: Issue #%d 判定为应关闭: %s", state.issue_number, close_reason)
                await self._close_issue(state, close_reason)
                return

            # 就绪
            if action == "ready" or state.questions_asked >= max_q:
                state.status = "READY"
                state.gathered_summary = result.get("understanding", state.title)
                self._save(state)
                logger.info("Tracker: Issue #%d 信息就绪 (action=%s), 通知 developer",
                             state.issue_number, action)
                await self._notify_developer(state, result)
                return

            # 追问 — 人格化后再发布
            question = result.get("question", "")
            if question:
                logger.debug("Tracker: Issue #%d 准备追问 (q%d): %s",
                             state.issue_number, state.questions_asked + 1, question[:80])
                persona_question = await self._persona_question(state, question)
                from .api import post_issue_comment
                await post_issue_comment(state.repo, state.issue_number, persona_question, self._config)
                state.add_conversation("assistant", persona_question)
                state.questions_asked += 1
                state.status = "AWAITING_RESPONSE"
                self._save(state)
                logger.info("Tracker: Issue #%d 已追问 (q%d): %s",
                            state.issue_number, state.questions_asked, persona_question[:80])
            else:
                logger.warning("Tracker: Issue #%d action=ask 但无追问内容，跳过", state.issue_number)
            return

    async def _close_issue(self, state: IssueState, reason: str) -> None:
        from .api import close_issue as api_close_issue
        from .closer import _generate_close_comment

        logger.debug("Tracker: Issue #%d 生成关闭评论...", state.issue_number)
        close_msg = await _generate_close_comment(
            {"number": state.issue_number, "title": state.title, "body": state.body},
            state.repo, self._engine_proxy, reason,
        )
        await api_close_issue(state.repo, state.issue_number, close_msg, self._config)
        state.status = "CLOSED"
        self._save(state)
        logger.info("Tracker: Issue #%d 已关闭 (reason=%s)", state.issue_number, reason)

        admin_id = self._config.get("admin_user_id", "")
        if not admin_id:
            from .webhook import _resolve_admin_id
            admin_id = _resolve_admin_id(self._adapter)
        if admin_id and self._adapter:
            try:
                prompt = f"""以你的角色身份通知管理员一个 Issue 已自动关闭。

Issue #{state.issue_number}: {state.title}
原因: {reason}

用角色口吻简述，1句话。"""
                persona_msg = await self._engine_proxy.generate_raw(prompt, inject_persona=True, model=self._config.get("model", ""))
                msg = persona_msg.strip() or f"Issue #{state.issue_number} 已自动关闭: {reason}"
            except Exception:
                msg = f"Issue #{state.issue_number}: {state.title} 已自动关闭\n仓库: {state.repo}\n原因: {reason}"
            await self._adapter.send_private_message(admin_id, msg)

    async def _persona_question(self, state: IssueState, base_question: str) -> str:
        """将功能性追问转为角色化表达（inject_persona=True）。"""
        try:
            prompt = f"""你正在 GitHub Issue 下以你的角色身份追问用户一个问题。

Issue #{state.issue_number}: {state.title}

要问的核心问题: {base_question}

请用你的角色口吻重新表述这个问题，保持友好、自然。只输出最终问题正文。"""
            result = await self._engine_proxy.generate_raw(prompt, inject_persona=True, model=self._config.get("model", ""))
            return result.strip() or base_question
        except Exception:
            return base_question

    async def _notify_developer(self, state: IssueState, result: dict[str, Any]) -> None:
        from .webhook import _resolve_admin_id
        admin_id = _resolve_admin_id(self._adapter)
        if not admin_id or not self._adapter:
            logger.warning("Tracker: Issue #%d 就绪但无法通知 developer (admin=%s adapter=%s)",
                           state.issue_number, bool(admin_id), bool(self._adapter))
            return
        approach = result.get("approach", "待分析")
        try:
            prompt = f"""以你的角色身份向项目管理员发送一条简短通知。

Issue #{state.issue_number}: {state.title}
理解: {state.gathered_summary}
修复方案: {approach}
任务ID: {state.task_id}

用你的角色口吻告诉管理员这个 Issue 已就绪，回复 /gh {state.task_id} auto 即可启动修复。1-2句话。"""
            persona_msg = await self._engine_proxy.generate_raw(prompt, inject_persona=True, model=self._config.get("model", ""))
            msg = persona_msg.strip() or f"[READY] Issue #{state.issue_number} 信息已就绪，回复 /gh {state.task_id} auto 启动修复"
        except Exception:
            msg = f"[READY] Issue #{state.issue_number}: {state.title}\n仓库: {state.repo}\n回复 /gh {state.task_id} auto 启动自动修复"
        await self._adapter.send_private_message(admin_id, msg)
        logger.info("Tracker: Issue #%d 已通知 developer (admin=%s)", state.issue_number, admin_id)
