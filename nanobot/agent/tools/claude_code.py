"""Claude Code tool - invoke Claude Code CLI for coding tasks."""

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ClaudeCodeTool(Tool):
    """Tool to invoke Claude Code CLI for coding tasks.
    
    Allows the agent to programmatically call Claude Code to:
    - Fix bugs
    - Implement features
    - Refactor code
    - Run git operations
    """
    
    def __init__(self, timeout: int | None = None):
        self._timeout = timeout
    
    @property
    def name(self) -> str:
        return "claude_code"
    
    @property
    def description(self) -> str:
        return (
            "调用 Claude Code 在指定项目目录中执行编码任务。"
            "适用于修复BUG、实现功能、重构代码等需要理解项目上下文的任务。"
            "Claude Code 会自动分析项目结构、修改代码、执行 git 操作。"
            "完成后请用 exec 工具执行构建和部署命令。"
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "详细的任务描述，包括要修复的问题或要实现的功能。"
                        "应包含足够的上下文让 Claude Code 理解需求。"
                        "完成后要求 git add && git commit。"
                    ),
                },
                "working_dir": {
                    "type": "string",
                    "description": "项目源码根目录的绝对路径",
                },
            },
            "required": ["task", "working_dir"],
        }
    
    async def execute(self, task: str, working_dir: str, **kwargs: Any) -> str:
        path = Path(working_dir)
        if not path.exists():
            return f"Error: 目录不存在: {working_dir}"
        if not path.is_dir():
            return f"Error: 不是目录: {working_dir}"
        
        cmd = "claude --print --dangerously-skip-permissions --output-format json"
        
        logger.info(f"ClaudeCodeTool: executing in {working_dir}")
        
        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=working_dir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                if self._timeout:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(input=task.encode("utf-8")),
                        timeout=self._timeout,
                    )
                else:
                    stdout, stderr = await process.communicate(
                        input=task.encode("utf-8")
                    )
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Claude Code 执行超时 ({self._timeout}s)"
            
            output = stdout.decode("utf-8", errors="replace")
            error_output = stderr.decode("utf-8", errors="replace")
            
            # Parse JSON output
            result_text = output
            try:
                if output.strip():
                    json_output = json.loads(output)
                    if "result" in json_output:
                        result_text = json_output["result"]
                    elif "content" in json_output:
                        result_text = json_output["content"]
            except json.JSONDecodeError:
                pass
            
            if process.returncode != 0 and error_output:
                result = f"⚠️ Claude Code 返回错误:\n{error_output[:2000]}"
                if result_text:
                    result += f"\n\n输出:\n{result_text[:3000]}"
                return result
            
            if not result_text:
                return "(Claude Code 无输出)"
            
            # Truncate long output
            if len(result_text) > 4000:
                result_text = result_text[:4000] + f"\n\n... (已截断，共 {len(result_text)} 字符)"
            
            logger.info("ClaudeCodeTool: task completed")
            return result_text
            
        except FileNotFoundError:
            return (
                "Error: 未找到 claude 命令。"
                "请确保已安装 Claude Code: npm install -g @anthropic-ai/claude-code"
            )
        except Exception as e:
            logger.error(f"ClaudeCodeTool error: {e}")
            return f"Error: 执行 Claude Code 时出错: {str(e)}"
