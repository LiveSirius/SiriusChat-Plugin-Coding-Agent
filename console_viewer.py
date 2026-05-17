from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# ANSI 颜色常量
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"
C_BLUE = "\033[34m"
C_WHITE = "\033[37m"


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _print_phase(name: str, detail: str) -> None:
    phase_map = {
        "PREPARATION": (f"{C_CYAN}工作区准备", C_CYAN),
        "ANALYSIS": (f"{C_YELLOW}代码分析", C_YELLOW),
        "MODIFICATION": (f"{C_MAGENTA}代码修改", C_MAGENTA),
        "VALIDATION": (f"{C_BLUE}测试验证", C_BLUE),
        "COMMIT": (f"{C_GREEN}提交与PR", C_GREEN),
    }
    label, color = phase_map.get(name, (name, C_WHITE))
    print(f"\n{C_BOLD}{'=' * 60}{C_RESET}")
    print(f"{C_BOLD}{label}{C_RESET}  {C_DIM}{_ts()}{C_RESET}")
    if detail:
        print(f"  {color}{detail}{C_RESET}")
    print(f"{C_BOLD}{'-' * 60}{C_RESET}")


def _print_think(text: str) -> None:
    for line in text.split("\n"):
        if len(line) > 120:
            line = line[:117] + "..."
        print(f"{C_WHITE}  {line}{C_RESET}")


def _print_reasoning(text: str) -> None:
    for line in text.split("\n"):
        if len(line) > 120:
            line = line[:117] + "..."
        print(f"{C_DIM}  [思考] {line}{C_RESET}")


def _print_tool_call(name: str, arguments: dict) -> None:
    args_str = ", ".join(f"{k}={repr(v)[:60]}" for k, v in arguments.items())
    print(f"  {C_YELLOW}调用工具: {name}({args_str}){C_RESET}")


def _print_tool_result(name: str, result: str, success: bool) -> None:
    icon = "OK" if success else "FAIL"
    color = C_GREEN if success else C_RED
    print(f"  {color}{icon} {name} 返回:{C_RESET}")
    for line in result.split("\n")[:10]:
        print(f"     {C_DIM}{line[:120]}{C_RESET}")


def _print_test_run(command: str, success: bool, stdout: str, stderr: str) -> None:
    if success:
        print(f"  {C_GREEN}OK {command} -- 全部通过{C_RESET}")
    else:
        print(f"  {C_RED}FAIL {command} -- 测试失败{C_RESET}")
        for line in stderr.split("\n")[:5]:
            if line.strip():
                print(f"     {C_RED}{line[:120]}{C_RESET}")


def _print_retry(attempt: int, max_retries: int, reason: str) -> None:
    print(f"\n  {C_YELLOW}第 {attempt}/{max_retries} 次重试{C_RESET}")
    print(f"  {C_DIM}原因: {reason[:200]}{C_RESET}")


def _print_error(message: str) -> None:
    print(f"  {C_RED}错误: {message[:300]}{C_RESET}")


def _print_done(success: bool, summary: str, pr_url: str) -> None:
    print(f"\n{C_BOLD}{'=' * 60}{C_RESET}")
    if success:
        print(f"{C_GREEN}{C_BOLD}修复完成！{C_RESET}")
    else:
        print(f"{C_RED}{C_BOLD}修复失败{C_RESET}")
    if summary:
        print(f"  {summary}")
    if pr_url:
        print(f"  {C_BLUE}{pr_url}{C_RESET}")
    print(f"{C_BOLD}{'=' * 60}{C_RESET}")


_EVENT_HANDLERS = {
    "phase": lambda d: _print_phase(d["name"], d.get("detail", "")),
    "think": lambda d: _print_think(d["text"]),
    "reasoning": lambda d: _print_reasoning(d["text"]),
    "tool_call": lambda d: _print_tool_call(d["name"], d["arguments"]),
    "tool_result": lambda d: _print_tool_result(d["name"], d["result"], d.get("success", True)),
    "test_run": lambda d: _print_test_run(d["command"], d["success"], d.get("stdout", ""), d.get("stderr", "")),
    "retry": lambda d: _print_retry(d["attempt"], d["max_retries"], d.get("reason", "")),
    "error": lambda d: _print_error(d["message"]),
    "done": lambda d: _print_done(d["success"], d.get("summary", ""), d.get("pr_url", "")),
}


def main() -> None:
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <stream_file> [--keep-open]")
        sys.exit(1)

    stream_path = Path(sys.argv[1])
    keep_open = "--keep-open" in sys.argv

    print(f"{C_BOLD}{C_CYAN}Sirius Chat - GitHub Agent 实时交互流{C_RESET}")
    print(f"{C_DIM}  等待 AI 开始工作...{C_RESET}\n")

    waited = 0
    while not stream_path.exists() and waited < 30:
        time.sleep(0.5)
        waited += 0.5

    if not stream_path.exists():
        print(f"{C_RED}等待超时：流文件未创建 ({stream_path}){C_RESET}")
        if not keep_open:
            time.sleep(3)
        else:
            input("按回车键关闭...")
        return

    last_pos = 0
    done_received = False

    try:
        while True:
            try:
                content = stream_path.read_text(encoding="utf-8")
            except Exception:
                time.sleep(0.1)
                continue

            lines = content.splitlines()[last_pos:]
            all_lines = content.splitlines()
            last_pos = len(all_lines)

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    event_type = event.get("type", "")
                    handler = _EVENT_HANDLERS.get(event_type)
                    if handler:
                        handler(event.get("data", {}))
                except json.JSONDecodeError:
                    pass

                if event.get("type") == "done":
                    done_received = True

            if done_received:
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        pass

    if not keep_open and done_received:
        print(f"\n{C_DIM}窗口将在 30 秒后自动关闭...{C_RESET}")
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            pass
    else:
        input(f"\n{C_DIM}按回车键关闭窗口...{C_RESET}")


if __name__ == "__main__":
    main()
