"""Swarm tool — exposes the Jarvis orchestrator as a tool the main agent can invoke."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.jarvis.orchestrator import Orchestrator


class SwarmTool(Tool):
    """Tool that delegates complex tasks to the Jarvis agent swarm."""

    def __init__(self, orchestrator: "Orchestrator"):
        self._orchestrator = orchestrator
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    @property
    def name(self) -> str:
        return "swarm"

    @property
    def description(self) -> str:
        return (
            "Delegate a complex task to the Jarvis agent swarm. "
            "The swarm has specialized agents (researcher, coder, qa, writer) that "
            "collaborate to complete the task. The coder agent uses Claude Code for implementation. "
            "Use this for tasks that benefit from multiple specialists working together, "
            "e.g. 'research X then implement Y then test it'."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task to delegate to the agent swarm. Be specific and detailed.",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, **kwargs: Any) -> str:
        """Execute the swarm orchestration."""
        session_id = f"{self._origin_channel}_{self._origin_chat_id}"
        # Clean session_id for filesystem safety
        session_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

        result = await self._orchestrator.handle(
            user_request=task,
            session_id=session_id,
        )
        return result
