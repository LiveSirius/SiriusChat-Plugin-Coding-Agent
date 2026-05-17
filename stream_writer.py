from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class StreamWriter:
    """向临时日志文件写入结构化事件，供 console_viewer.py 消费。"""

    def __init__(self, stream_file: Path) -> None:
        self._file = stream_file
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._file.open("w", encoding="utf-8", buffering=1)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        line = json.dumps({"ts": time.time(), "type": event_type, "data": data}, ensure_ascii=False)
        self._fh.write(line + "\n")
        self._fh.flush()

    def phase(self, name: str, detail: str = "") -> None:
        """输出阶段切换标记。"""
        self._emit("phase", {"name": name, "detail": detail})

    def think(self, text: str) -> None:
        """输出 LLM 推理片段。"""
        self._emit("think", {"text": text})

    def reasoning(self, text: str) -> None:
        """输出 LLM 推理过程（thinking/reasoning tokens）。"""
        self._emit("reasoning", {"text": text})

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        """输出工具调用开始。"""
        self._emit("tool_call", {"name": name, "arguments": arguments})

    def tool_result(self, name: str, result: str, success: bool = True) -> None:
        """输出工具调用结果。结果过长时截断。"""
        truncated = result[:500] + ("..." if len(result) > 500 else "")
        self._emit("tool_result", {"name": name, "result": truncated, "success": success})

    def test_run(self, command: str, success: bool, stdout: str, stderr: str) -> None:
        """输出测试执行结果。"""
        self._emit("test_run", {
            "command": command,
            "success": success,
            "stdout": stdout[:300],
            "stderr": stderr[:300],
        })

    def retry(self, attempt: int, max_retries: int, reason: str) -> None:
        """输出重试信息。"""
        self._emit("retry", {"attempt": attempt, "max_retries": max_retries, "reason": reason})

    def error(self, message: str) -> None:
        """输出错误。"""
        self._emit("error", {"message": message})

    def done(self, success: bool, summary: str, pr_url: str = "") -> None:
        """输出最终结果。viewer 读取到此事件后进入倒计时退出。"""
        self._emit("done", {"success": success, "summary": summary, "pr_url": pr_url})

    def close(self) -> None:
        self._fh.close()
