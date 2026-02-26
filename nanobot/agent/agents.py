"""Predefined agent definitions for the Swarm architecture.

Each agent has:
- name: identifier used for session persistence
- instructions: system prompt (can be a callable receiving context_variables)
- functions: list of Python callables exposed as LLM tools
- model: optional model override (None = use default)
"""

import asyncio
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.swarm import Agent, SwarmResult

if TYPE_CHECKING:
    from nanobot.agent.context import ContextBuilder


# ---------------------------------------------------------------------------
# Shared tool functions (available to multiple agents)
# ---------------------------------------------------------------------------

async def _run_claude_code(task: str, working_dir: str) -> str:
    """Execute a coding task via Claude Code CLI.
    
    Spawns the ``claude`` process with --print mode and passes
    the task description via stdin.
    """
    path = Path(working_dir)
    if not path.exists():
        return f"Error: 目录不存在: {working_dir}"
    if not path.is_dir():
        return f"Error: 不是目录: {working_dir}"

    cmd = "claude --print --dangerously-skip-permissions --output-format json"
    logger.info(f"Coder agent: executing Claude Code in {working_dir}")

    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            cwd=working_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(input=task.encode("utf-8"))
        output = stdout.decode("utf-8", errors="replace")
        error_output = stderr.decode("utf-8", errors="replace")

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

        if len(result_text) > 4000:
            result_text = result_text[:4000] + f"\n\n... (已截断，共 {len(result_text)} 字符)"

        return result_text
    except FileNotFoundError:
        return (
            "Error: 未找到 claude 命令。"
            "请确保已安装 Claude Code: npm install -g @anthropic-ai/claude-code"
        )
    except Exception as e:
        logger.error(f"Coder agent error: {e}")
        return f"Error: 执行 Claude Code 时出错: {str(e)}"


# ---------------------------------------------------------------------------
# Handoff functions
# ---------------------------------------------------------------------------

def handoff_to_coder(task: str = "", context_variables: dict = {}) -> SwarmResult:
    """将任务移交给 Coder Agent（使用 Claude Code 执行编码任务）。

    当需要修改代码、修复 bug、实现新功能时调用此函数。

    Args:
        task: 要移交的任务描述。
    """
    return SwarmResult(
        value=f"正在移交给 Coder Agent...{(' 任务: ' + task) if task else ''}",
        agent=create_coder_agent(),
    )


def handoff_to_searcher(task: str = "", context_variables: dict = {}) -> SwarmResult:
    """将任务移交给 Searcher Agent（负责搜索和调研）。

    当需要搜索网络、查找信息、调研技术方案时调用此函数。

    Args:
        task: 要移交的任务描述。
    """
    return SwarmResult(
        value=f"正在移交给 Searcher Agent...{(' 任务: ' + task) if task else ''}",
        agent=create_searcher_agent(),
    )


def handoff_to_triage(task: str = "", context_variables: dict = {}) -> SwarmResult:
    """将控制权交还给 Triage Agent（主协调 agent）。

    当当前任务完成后调用此函数，返回总指挥继续处理。

    Args:
        task: 完成的任务总结。
    """
    return SwarmResult(
        value=f"任务完成，返回主 Agent。{(' ' + task) if task else ''}",
        agent=create_triage_agent(),
    )


# ---------------------------------------------------------------------------
# Agent factories
# ---------------------------------------------------------------------------

def create_triage_agent(context_builder: "ContextBuilder | None" = None) -> Agent:
    """Create the triage (coordinator) agent.
    
    This is the main agent that receives user messages, understands
    intent, and either handles simple tasks directly or hands off
    to specialized agents.
    
    Args:
        context_builder: Optional ContextBuilder for full system prompt.
    """
    if context_builder:
        def instructions(ctx: dict) -> str:
            return _triage_instructions_full(ctx, context_builder)
    else:
        instructions = _triage_instructions

    return Agent(
        name="triage",
        instructions=instructions,
        functions=[
            handoff_to_coder,
            handoff_to_searcher,
        ],
    )


def create_coder_agent() -> Agent:
    """Create the coder agent.
    
    Specialized in coding tasks, uses Claude Code CLI
    for the actual code modifications.
    """
    return Agent(
        name="coder",
        instructions=_coder_instructions,
        functions=[
            _run_claude_code,
            handoff_to_triage,
        ],
        # model=None → uses Claude Code CLI internally (strongest model)
    )


def create_searcher_agent() -> Agent:
    """Create the searcher agent.
    
    Specialized in web search, information retrieval,
    and research tasks.
    """
    # web_search, web_fetch, read_file are injected dynamically
    return Agent(
        name="searcher",
        instructions=_searcher_instructions,
        functions=[
            handoff_to_triage,
        ],
    )


# ---------------------------------------------------------------------------
# Agent instructions (system prompts)
# ---------------------------------------------------------------------------

def _triage_instructions_full(context_variables: dict, context_builder: "ContextBuilder") -> str:
    """Build full triage instructions using ContextBuilder for rich context."""
    channel = context_variables.get("channel", "unknown")
    chat_id = context_variables.get("chat_id", "unknown")
    
    # Get the full system prompt from ContextBuilder (includes skills, memory, bootstrap)
    base_prompt = context_builder.build_system_prompt()
    
    return f"""{base_prompt}

---

# Agent 协作模式

你是 nanobot 的总指挥（Triage Agent），拥有以上所有知识和能力。

## 你的职责
1. 理解用户意图
2. 对于简单任务（闲聊、查状态、读文件、执行命令、检查反馈等），直接使用你的工具处理
3. 对于编码任务（修 bug、写代码、重构等），调用 handoff_to_coder 移交给 Coder Agent
4. 对于搜索/调研任务，调用 handoff_to_searcher 移交给 Searcher Agent

## 当前上下文
- 消息来源: {channel}:{chat_id}

## 注意事项
- 始终用中文回复
- 简单任务自己搞定，复杂编码任务转交 Coder Agent
- 你可以直接使用 exec 工具执行 curl、shell 命令等
- 你可以直接使用 read_file、write_file、list_dir 等工具
- 如果用户说"检查反馈"，你应该使用 exec 调用 curl 来获取数据并分析
- 如果用户提到具体的目录路径和编码任务，移交给 Coder Agent
"""


def _triage_instructions(context_variables: dict) -> str:
    """Fallback triage instructions without ContextBuilder."""
    channel = context_variables.get("channel", "unknown")
    chat_id = context_variables.get("chat_id", "unknown")
    return f"""# Triage Agent

你是 nanobot 的总指挥（Triage Agent）。

## 你的职责
1. 理解用户意图
2. 对于简单任务（闲聊、查状态、读文件等），直接处理
3. 对于编码任务（修 bug、写代码、重构等），调用 handoff_to_coder 移交给 Coder Agent
4. 对于搜索/调研任务，调用 handoff_to_searcher 移交给 Searcher Agent

## 当前上下文
- 消息来源: {channel}:{chat_id}

## 注意事项
- 始终用中文回复
- 简单任务自己搞定，复杂任务转交
- 如果用户提到具体的目录路径和编码任务，一定要移交给 Coder Agent
"""


_coder_instructions = """# Coder Agent

你是 nanobot 的编码专家（Coder Agent）。

## 你的职责
1. 使用 _run_claude_code 工具来执行编码任务
2. Claude Code 会自动分析项目结构、修改代码、执行 git 操作
3. 任务完成后，调用 handoff_to_triage 将控制权交还给主 agent

## 使用 _run_claude_code 的要求
- task: 详细描述任务内容，包括要修改的文件、问题描述、期望结果
- working_dir: 项目根目录的绝对路径

## 注意事项
- 用中文和用户交流
- 完成任务后别忘了 handoff_to_triage
- 如果 Claude Code 报错，分析错误后可以重试
"""


_searcher_instructions = """# Searcher Agent

你是 nanobot 的搜索专家（Searcher Agent）。

## 你的职责
1. 使用搜索工具查找信息
2. 使用网页抓取工具获取详细内容
3. 整理和总结搜索结果
4. 完成后调用 handoff_to_triage 将控制权交还给主 agent

## 注意事项
- 用中文和用户交流
- 搜索结果要精简，突出关键信息
- 完成搜索后别忘了 handoff_to_triage
"""
