"""Agent pool — runs individual agents with role-specific prompts and tools."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.jarvis.memory import SharedMemory
from nanobot.jarvis.roles import RoleSpec


@dataclass
class AgentResult:
    """Result from a single agent execution."""

    role: str
    task_id: str
    task: str
    output: str
    status: str  # "ok" | "error" | "timeout"
    duration_s: float = 0.0


class AgentPool:
    """
    Manages execution of role-specialized agents.

    Two execution modes:
    - LLM loop: uses nanobot's LLM provider + tool registry (researcher, qa, writer)
    - Claude Code: shells out to `claude -p` for coding tasks (coder)
    """

    def __init__(
        self,
        provider: Any,  # LLMProvider
        workspace: Path,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
        brave_api_key: str | None = None,
        exec_config: Any | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.provider = provider
        self.workspace = workspace
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config
        self.restrict_to_workspace = restrict_to_workspace

    async def run_agent(
        self,
        role: RoleSpec,
        task_id: str,
        task: str,
        shared_mem: SharedMemory,
    ) -> AgentResult:
        """Run a single agent. Dispatches to Claude Code or LLM loop based on role config."""
        start = asyncio.get_event_loop().time()

        try:
            if role.use_claude_code:
                output = await self._run_claude_code(role, task, shared_mem)
            else:
                output = await self._run_llm_loop(role, task, shared_mem)

            duration = asyncio.get_event_loop().time() - start
            shared_mem.write(role.name, task_id, output)

            return AgentResult(
                role=role.name, task_id=task_id, task=task,
                output=output, status="ok", duration_s=duration,
            )
        except asyncio.TimeoutError:
            duration = asyncio.get_event_loop().time() - start
            return AgentResult(
                role=role.name, task_id=task_id, task=task,
                output="Agent timed out.", status="timeout", duration_s=duration,
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start
            logger.error("Agent {} failed on {}: {}", role.name, task_id, e)
            return AgentResult(
                role=role.name, task_id=task_id, task=task,
                output=f"Error: {e}", status="error", duration_s=duration,
            )

    async def _run_claude_code(
        self, role: RoleSpec, task: str, shared_mem: SharedMemory,
    ) -> str:
        """Execute task via Claude Code CLI (`claude -p`)."""
        briefing = shared_mem.get_briefing()

        prompt_parts = []
        if role.system_prompt:
            prompt_parts.append(role.system_prompt)
        if briefing and "(No prior findings" not in briefing:
            prompt_parts.append(f"\n{briefing}\n")
        prompt_parts.append(f"\n## Task\n{task}")

        full_prompt = "\n".join(prompt_parts)

        # Find claude CLI
        claude_bin = shutil.which("claude")
        if not claude_bin:
            return "Error: claude CLI not found in PATH. Install Claude Code first."

        logger.info("Coder agent delegating to Claude Code: {}", task[:80])

        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", full_prompt, "--output-format", "text",
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=600,  # 10 min max
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        output = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                output += f"\n\n[stderr]\n{err}"

        return output or "(Claude Code returned empty output)"

    async def _run_llm_loop(
        self, role: RoleSpec, task: str, shared_mem: SharedMemory,
    ) -> str:
        """Run a standard LLM tool-use loop for non-coder agents."""
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
        from nanobot.agent.tools.shell import ExecTool
        from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
        from nanobot.config.schema import ExecToolConfig

        # Build tool registry based on role's allowed tools
        tools = ToolRegistry()
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        exec_cfg = self.exec_config or ExecToolConfig()

        tool_map = {
            "file_read": lambda: ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir),
            "file_write": lambda: WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir),
            "file_edit": lambda: EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir),
            "list_dir": lambda: ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir),
            "shell": lambda: ExecTool(
                working_dir=str(self.workspace), timeout=exec_cfg.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=exec_cfg.path_append,
            ),
            "web_search": lambda: WebSearchTool(api_key=self.brave_api_key),
            "web_fetch": lambda: WebFetchTool(),
        }

        for tool_name in role.tools:
            if tool_name in tool_map:
                tools.register(tool_map[tool_name]())

        # Build messages
        briefing = shared_mem.get_briefing()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        system_parts = [f"# Role: {role.display_name}\n\nCurrent time: {now}\n"]
        if role.system_prompt:
            system_parts.append(role.system_prompt)
        if briefing and "(No prior findings" not in briefing:
            system_parts.append(briefing)
        system_parts.append(
            "\nComplete the assigned task. Be thorough but concise. "
            "When done, provide a clear summary of your findings or actions."
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(system_parts)},
            {"role": "user", "content": task},
        ]

        model = role.model or self.model or self.provider.get_default_model()
        max_iter = role.max_iterations
        final_result: str | None = None

        for iteration in range(max_iter):
            response = await self.provider.chat(
                messages=messages,
                tools=tools.get_definitions() or None,
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id, "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tool_call_dicts,
                })

                for tc in response.tool_calls:
                    logger.debug("Swarm agent [{}] tool: {}({})", role.name, tc.name,
                                 json.dumps(tc.arguments, ensure_ascii=False)[:200])
                    result = await tools.execute(tc.name, tc.arguments)
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "name": tc.name, "content": result,
                    })
            else:
                final_result = response.content
                break

        return final_result or "(Agent completed without producing a response)"
