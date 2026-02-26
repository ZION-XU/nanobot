"""Agent loop: the core processing engine."""

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.swarm import Agent, Swarm, SwarmResult
from nanobot.agent.agents import (
    create_triage_agent,
    create_coder_agent,
    create_searcher_agent,
)
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager


class AgentLoop:
    """
    The agent loop is the core processing engine.
    
    It:
    1. Receives messages from the bus
    2. Routes to the active Swarm agent
    3. Manages handoffs between agents
    4. Sends responses back
    """
    
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        from nanobot.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        
        self.context = ContextBuilder(workspace)
        self.sessions = SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
        )
        
        self._running = False
        self._register_default_tools()
        
        # Swarm engine
        self.swarm = Swarm(provider=provider, default_model=self.model)
        
        # Agent registry
        self._agents: dict[str, Agent] = {}
        self._init_agents()

    def _init_agents(self) -> None:
        """Initialize all swarm agents with injected tool functions."""
        # Create base agents
        triage = create_triage_agent(context_builder=self.context)
        coder = create_coder_agent()
        searcher = create_searcher_agent()

        # Inject shared tool functions into agents
        shared_tools = self._create_shared_tool_functions()
        triage.functions = list(triage.functions) + shared_tools
        searcher.functions = list(searcher.functions) + self._create_search_tool_functions()

        self._agents = {
            "triage": triage,
            "coder": coder,
            "searcher": searcher,
        }

    def _create_shared_tool_functions(self) -> list:
        """Create shared tool functions from the ToolRegistry.
        
        Wraps existing Tool instances as plain Python functions
        so they can be used by the Swarm engine.
        """
        functions = []

        # read_file
        read_tool = self.tools.get("read_file")
        if read_tool:
            async def read_file(path: str) -> str:
                """读取文件内容。"""
                return await self.tools.execute("read_file", {"path": path})
            functions.append(read_file)

        # write_file
        write_tool = self.tools.get("write_file")
        if write_tool:
            async def write_file(path: str, content: str) -> str:
                """写入文件内容。"""
                return await self.tools.execute("write_file", {"path": path, "content": content})
            functions.append(write_file)

        # edit_file
        edit_tool = self.tools.get("edit_file")
        if edit_tool:
            async def edit_file(path: str, old_text: str, new_text: str) -> str:
                """编辑文件，替换指定文本。"""
                return await self.tools.execute("edit_file", {"path": path, "old_text": old_text, "new_text": new_text})
            functions.append(edit_file)

        # list_dir
        list_tool = self.tools.get("list_dir")
        if list_tool:
            async def list_dir(path: str) -> str:
                """列出目录内容。"""
                return await self.tools.execute("list_dir", {"path": path})
            functions.append(list_dir)

        # exec
        exec_tool = self.tools.get("exec")
        if exec_tool:
            async def exec(command: str) -> str:
                """执行 shell 命令。"""
                return await self.tools.execute("exec", {"command": command})
            functions.append(exec)

        # message
        msg_tool = self.tools.get("message")
        if msg_tool:
            async def message(content: str, channel: str, to: str) -> str:
                """发送消息给用户（通过指定渠道）。"""
                return await self.tools.execute("message", {"content": content, "channel": channel, "to": to})
            functions.append(message)

        # spawn
        spawn_tool = self.tools.get("spawn")
        if spawn_tool:
            async def spawn(task: str, label: str = "") -> str:
                """在后台启动一个 subagent 执行独立任务。"""
                params: dict[str, Any] = {"task": task}
                if label:
                    params["label"] = label
                return await self.tools.execute("spawn", params)
            functions.append(spawn)

        # cron
        cron_tool = self.tools.get("cron")
        if cron_tool:
            async def cron(action: str, name: str = "", message: str = "", schedule: str = "", job_id: str = "", deliver: bool = False, channel: str = "", to: str = "") -> str:
                """管理定时任务。action: add/list/remove/enable/disable"""
                params: dict[str, Any] = {"action": action}
                if name:
                    params["name"] = name
                if message:
                    params["message"] = message
                if schedule:
                    params["schedule"] = schedule
                if job_id:
                    params["job_id"] = job_id
                if deliver:
                    params["deliver"] = deliver
                if channel:
                    params["channel"] = channel
                if to:
                    params["to"] = to
                return await self.tools.execute("cron", params)
            functions.append(cron)

        return functions

    def _create_search_tool_functions(self) -> list:
        """Create search-specific tool functions for the searcher agent."""
        functions = []

        search_tool = self.tools.get("web_search")
        if search_tool:
            async def web_search(query: str) -> str:
                """搜索网络信息。"""
                return await self.tools.execute("web_search", {"query": query})
            functions.append(web_search)

        fetch_tool = self.tools.get("web_fetch")
        if fetch_tool:
            async def web_fetch(url: str) -> str:
                """获取网页内容。"""
                return await self.tools.execute("web_fetch", {"url": url})
            functions.append(web_fetch)

        # Also give searcher read_file
        read_tool = self.tools.get("read_file")
        if read_tool:
            async def read_file(path: str) -> str:
                """读取文件内容。"""
                return await self.tools.execute("read_file", {"path": path})
            functions.append(read_file)

        return functions

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools
        self.tools.register(ReadFileTool())
        self.tools.register(WriteFileTool())
        self.tools.register(EditFileTool())
        self.tools.register(ListDirTool())
        
        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.exec_config.restrict_to_workspace,
        ))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        
        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message via the Swarm engine.

        The active agent is restored from the session so that
        multi-turn handoff state is preserved.
        """
        # Handle system messages (subagent announces)
        if msg.channel == "system":
            return await self._process_system_message(msg)

        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}")

        # Get or create session
        session = self.sessions.get_or_create(msg.session_key)

        # Update tool contexts (message, spawn, cron need channel/chat_id)
        self._update_tool_contexts(msg.channel, msg.chat_id)

        # Determine active agent (restore from session or default to triage)
        active_agent_name = session.metadata.get("active_agent", "triage")
        agent = self._agents.get(active_agent_name, self._agents["triage"])

        # Build context variables
        ctx = session.metadata.get("context_variables", {})
        ctx.update({
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "sender_id": msg.sender_id,
        })

        # Build messages from history
        history = session.get_history()
        history.append({"role": "user", "content": msg.content})

        # Run Swarm
        result = await self.swarm.run(
            agent=agent,
            messages=history,
            context_variables=ctx,
            max_turns=self.max_iterations,
        )

        # Extract final response
        final_content = None
        for m in reversed(result.messages):
            if m.get("role") == "assistant" and m.get("content"):
                # Skip messages that are just tool-call wrappers
                if not m.get("tool_calls"):
                    final_content = m["content"]
                    break
        
        if not final_content:
            # Fallback: look for any assistant content
            for m in reversed(result.messages):
                if m.get("role") == "assistant" and m.get("content"):
                    final_content = m["content"]
                    break

        if not final_content:
            final_content = "任务已完成。"

        # Persist agent state and context
        session.metadata["active_agent"] = result.agent.name if result.agent else "triage"
        session.metadata["context_variables"] = result.context_variables

        # Save conversation to session
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        
        # Update tool contexts
        self._update_tool_contexts(origin_channel, origin_chat_id)
        
        # Use triage agent for system messages
        agent = self._agents["triage"]

        ctx = session.metadata.get("context_variables", {})
        ctx.update({
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        })

        history = session.get_history()
        history.append({"role": "user", "content": msg.content})

        result = await self.swarm.run(
            agent=agent,
            messages=history,
            context_variables=ctx,
            max_turns=self.max_iterations,
        )

        # Extract final response
        final_content = None
        for m in reversed(result.messages):
            if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls"):
                final_content = m["content"]
                break

        if not final_content:
            final_content = "后台任务已完成。"

        # Save to session
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        session.metadata["context_variables"] = result.context_variables
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """
        Process a message directly (for CLI, cron, or heartbeat usage).
        
        Routes through the Swarm engine just like regular messages.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content
        )
        
        response = await self._process_message(msg)
        return response.content if response else ""

    def _update_tool_contexts(self, channel: str, chat_id: str) -> None:
        """Update channel/chat_id context on tools that need it."""
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(channel, chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(channel, chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(channel, chat_id)
