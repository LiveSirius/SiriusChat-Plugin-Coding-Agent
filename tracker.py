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
        adapter: Any,
    ) -> None:
        self._store = data_store
        self._config = config
        self._engine_proxy = engine_proxy
        self._adapter = adapter
        self._task: asyncio.Task | None = None
        self._running = False

    def enqueue(self, issue_number: int, repo: str, title: str, body: str, labels: list[str]) -> str:
        import uuid
        task_id = uuid.uuid4().hex[:12]
        state = IssueState(
            issue_number=issue_number, repo=repo, title=title, body=body,
            labels=labels, task_id=task_id,
        )
        state.add_conversation("user", f"Issue #{issue_number}: {title}\n\n{body}")
        self._store.set(f"{_PREFIX}{task_id}", state.to_dict())
        logger.info("Tracker: Issue #%d (%s) 已入队, task_id=%s", issue_number, repo, task_id)
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
        logger.info("Tracker 后台循环已启动（间隔 %ds）", _TRACKER_TICK)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Tracker tick 异常")
            await asyncio.sleep(_TRACKER_TICK)

    async def _tick(self) -> None:
        for state in self.list_active():
            try:
                await self._process(state)
            except Exception:
                logger.exception("Tracker 处理 Issue #%d 异常", state.issue_number)

    async def _process(self, state: IssueState) -> None:
        # 1. 拉取新评论
        await self._fetch_new_comments(state)

        # 2. GATHERING_INFO → 分析是否就绪
        if state.status == "GATHERING_INFO":
            await self._try_gather(state)

        # 3. AWAITING_RESPONSE → 等待用户回复后重新分析
        elif state.status == "AWAITING_RESPONSE":
            if time.time() - state.last_activity > 120:
                await self._try_gather(state)

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
            logger.info("Tracker: Issue #%d 新评论 @%s", state.issue_number, user_login)

        # 有新评论时尝试调整标签
        if had_new and self._config.get("auto_label", True):
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
                    logger.info("Tracker: Issue #%d 补充标签: %s", state.issue_number, new_labels)
            except Exception as exc:
                logger.debug("Tracker: Issue #%d 标签调整失败: %s", state.issue_number, exc)

        self._save(state)

    async def _try_gather(self, state: IssueState) -> None:
        max_q = self._config.get("max_questions", 3)
        code_context: dict[str, str] = {}
        fetched_files: set[str] = set()

        # 多轮信息收集：最多 2 轮代码查看 + 1 轮最终决策
        for round_num in range(1, 4):
            result = await analyze_and_gather(state, self._engine_proxy, code_context or None)

            # 请求查看文件 → 获取后重新分析
            look_at = result.get("look_at_files", [])
            if look_at and round_num < 3:
                new_files = [f for f in look_at if f not in fetched_files]
                if new_files:
                    for file_path in new_files[:3]:
                        content = await get_file_content(state.repo, file_path, config=self._config)
                        if content:
                            code_context[file_path] = content
                            fetched_files.add(file_path)
                            logger.info("Tracker: Issue #%d 获取代码文件 %s", state.issue_number, file_path)
                    continue  # 重新分析（带新代码上下文）

            action = result.get("action", "ask")

            # 关单
            if action == "close":
                close_reason = result.get("close_reason", "经分析此 Issue 无需继续跟进")
                await self._close_issue(state, close_reason)
                return

            # 就绪
            if action == "ready" or state.questions_asked >= max_q:
                state.status = "READY"
                state.gathered_summary = result.get("understanding", state.title)
                self._save(state)
                logger.info("Tracker: Issue #%d 信息就绪，准备通知 developer", state.issue_number)
                await self._notify_developer(state, result)
                return

            # 追问
            question = result.get("question", "")
            if question:
                from .api import post_issue_comment
                await post_issue_comment(state.repo, state.issue_number, question, self._config)
                state.add_conversation("assistant", question)
                state.questions_asked += 1
                state.status = "AWAITING_RESPONSE"
                self._save(state)
                logger.info("Tracker: Issue #%d 追问 (%d): %s",
                            state.issue_number, state.questions_asked, question[:80])
            return

    async def _close_issue(self, state: IssueState, reason: str) -> None:
        from .api import close_issue as api_close_issue
        from .closer import _generate_close_comment

        close_msg = await _generate_close_comment(
            {"number": state.issue_number, "title": state.title, "body": state.body},
            state.repo, self._engine_proxy, reason,
        )
        await api_close_issue(state.repo, state.issue_number, close_msg, self._config)
        state.status = "CLOSED"
        self._save(state)
        logger.info("Tracker: Issue #%d 已关闭", state.issue_number)

        admin_id = self._config.get("admin_user_id", "")
        if admin_id and self._adapter:
            await self._adapter.send_private_message(
                admin_id,
                f"Issue #{state.issue_number}: {state.title} 已自动关闭\n"
                f"仓库: {state.repo}\n"
                f"原因: {reason}",
            )

    async def _notify_developer(self, state: IssueState, result: dict[str, Any]) -> None:
        admin_id = self._config.get("admin_user_id", "")
        if not admin_id or not self._adapter:
            return
        approach = result.get("approach", "待分析")
        msg = (
            f"[READY] Issue #{state.issue_number}: {state.title}\n"
            f"仓库: {state.repo}\n"
            f"理解: {state.gathered_summary}\n"
            f"方案: {approach}\n\n"
            f"回复 /gh {state.task_id} auto 启动自动修复"
        )
        await self._adapter.send_private_message(admin_id, msg)
        logger.info("Tracker: Issue #%d 已通知 developer", state.issue_number)
