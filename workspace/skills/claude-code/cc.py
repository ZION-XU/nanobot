#!/usr/bin/env python3
"""
Claude Code Session Manager

A standalone CLI tool that manages Claude Code sessions.
nanobot calls this via exec, keeping all logic outside nanobot core.

Usage:
    python cc.py enter <working_dir>     # Enter Claude Code mode
    python cc.py send <session_id> <msg> # Send message to Claude Code
    python cc.py exit <session_id>       # Exit Claude Code mode
    python cc.py status <session_id>     # Check session status
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Session storage directory
SESSIONS_DIR = Path.home() / ".nanobot" / "claude-sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def get_session_file(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def load_session(session_id: str) -> dict | None:
    path = get_session_file(session_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_session(session_id: str, data: dict) -> None:
    path = get_session_file(session_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_session(session_id: str) -> None:
    path = get_session_file(session_id)
    if path.exists():
        path.unlink()


async def run_claude(working_dir: str, prompt: str, claude_session_id: str | None = None) -> tuple[str, str | None]:
    """Run Claude Code and return (output, new_session_id)."""
    cmd = "claude --print --dangerously-skip-permissions"
    if claude_session_id:
        cmd += f" --resume {claude_session_id}"

    process = await asyncio.create_subprocess_shell(
        cmd,
        cwd=working_dir,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate(input=prompt.encode("utf-8"))

    output = stdout.decode("utf-8", errors="replace")
    error = stderr.decode("utf-8", errors="replace")

    if process.returncode != 0 and error:
        output = f"⚠️ Error:\n{error}\n\n{output}"

    # Try to extract session_id from output (if available)
    new_session_id = None
    # Claude Code may output session info in various formats

    return output, new_session_id


def cmd_enter(args):
    """Enter Claude Code mode."""
    working_dir = args.working_dir
    path = Path(working_dir)

    if not path.exists():
        print(json.dumps({"error": f"目录不存在: {working_dir}"}))
        return 1

    if not path.is_dir():
        print(json.dumps({"error": f"不是目录: {working_dir}"}))
        return 1

    # Generate session ID
    import hashlib
    import time
    session_id = hashlib.md5(f"{working_dir}{time.time()}".encode()).hexdigest()[:12]

    # Save session
    save_session(session_id, {
        "working_dir": str(path.resolve()),
        "claude_session_id": None,
        "created_at": time.time(),
    })

    result = {
        "session_id": session_id,
        "working_dir": str(path.resolve()),
        "message": f"✅ 已进入 Claude Code 模式\n📁 工作目录: {path.resolve()}\n\n现在发送消息会直接传给 Claude Code。\n发送「退出」退出此模式。"
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_send(args):
    """Send message to Claude Code."""
    session_id = args.session_id
    message = args.message

    session = load_session(session_id)
    if not session:
        print(json.dumps({"error": f"会话不存在: {session_id}"}))
        return 1

    # Run Claude Code
    output, new_claude_session = asyncio.run(
        run_claude(
            session["working_dir"],
            message,
            session.get("claude_session_id")
        )
    )

    # Update session if we got a new claude session id
    if new_claude_session:
        session["claude_session_id"] = new_claude_session
        save_session(session_id, session)

    # Truncate long output
    if len(output) > 4000:
        output = output[:4000] + f"\n\n... (截断，共 {len(output)} 字符)"

    result = {
        "session_id": session_id,
        "output": output
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_exit(args):
    """Exit Claude Code mode."""
    session_id = args.session_id

    session = load_session(session_id)
    if session:
        delete_session(session_id)
        print(json.dumps({"message": "✅ 已退出 Claude Code 模式，回到正常对话。"}))
    else:
        print(json.dumps({"message": "会话已结束。"}))
    return 0


def cmd_status(args):
    """Check session status."""
    session_id = args.session_id

    session = load_session(session_id)
    if session:
        print(json.dumps({
            "active": True,
            "working_dir": session["working_dir"],
            "session_id": session_id
        }))
    else:
        print(json.dumps({"active": False}))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Code Session Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # enter
    p_enter = subparsers.add_parser("enter", help="Enter Claude Code mode")
    p_enter.add_argument("working_dir", help="Working directory")
    p_enter.set_defaults(func=cmd_enter)

    # send
    p_send = subparsers.add_parser("send", help="Send message")
    p_send.add_argument("session_id", help="Session ID")
    p_send.add_argument("message", help="Message to send")
    p_send.set_defaults(func=cmd_send)

    # exit
    p_exit = subparsers.add_parser("exit", help="Exit Claude Code mode")
    p_exit.add_argument("session_id", help="Session ID")
    p_exit.set_defaults(func=cmd_exit)

    # status
    p_status = subparsers.add_parser("status", help="Check session status")
    p_status.add_argument("session_id", help="Session ID")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
