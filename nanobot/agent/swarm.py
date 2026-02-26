"""Swarm engine - Agent handoff orchestration inspired by OpenAI Swarm.

Core concepts:
- Agent: has instructions (system prompt), functions (tools), and optional model override
- Handoff: a function returns a SwarmResult with an Agent → control switches
- context_variables: shared dict across all agents in a run
"""

import asyncio
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Union

from loguru import logger

from nanobot.providers.base import LLMProvider


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Agent:
    """A Swarm agent definition.
    
    Each agent has its own system prompt (instructions), a set of
    callable functions (exposed as LLM tools), and an optional model
    override so that different agents can use different LLMs.
    """
    name: str
    instructions: str | Callable[[dict], str] = "You are a helpful agent."
    functions: list[Callable] = field(default_factory=list)
    model: str | None = None
    tool_choice: str | None = None


@dataclass
class SwarmResult:
    """Return value from an agent function.
    
    - value: text result shown to the LLM as the tool output
    - agent: if set, triggers a handoff to this agent
    - context_variables: merged into the shared context
    """
    value: str = ""
    agent: Agent | None = None
    context_variables: dict = field(default_factory=dict)


@dataclass
class SwarmResponse:
    """Final response from a Swarm run."""
    messages: list[dict[str, Any]] = field(default_factory=list)
    agent: Agent | None = None
    context_variables: dict = field(default_factory=dict)


# Sentinel name for context_variables injection
_CTX_VARS_NAME = "context_variables"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def function_to_tool_schema(func: Callable) -> dict[str, Any]:
    """Convert a Python function to an OpenAI-style tool schema.
    
    Uses the function's docstring as description and inspects
    type hints to build the parameters JSON Schema.  The special
    ``context_variables`` parameter is hidden from the schema.
    """
    sig = inspect.signature(func)
    doc = inspect.getdoc(func) or ""

    properties: dict[str, Any] = {}
    required: list[str] = []

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    for pname, param in sig.parameters.items():
        if pname == _CTX_VARS_NAME or pname == "self":
            continue
        prop: dict[str, Any] = {}
        annotation = param.annotation
        if annotation != inspect.Parameter.empty:
            prop["type"] = type_map.get(annotation, "string")
        else:
            prop["type"] = "string"
        properties[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _get_instructions(agent: Agent, context_variables: dict) -> str:
    """Resolve agent instructions (may be a callable)."""
    if callable(agent.instructions):
        return agent.instructions(context_variables)
    return agent.instructions


# ---------------------------------------------------------------------------
# Swarm engine
# ---------------------------------------------------------------------------

class Swarm:
    """Swarm runtime that manages agent switching and context passing.
    
    Usage::
    
        swarm = Swarm(provider, default_model="gpt-4o")
        response = await swarm.run(
            agent=triage_agent,
            messages=[{"role": "user", "content": "Hello"}],
            context_variables={"user_name": "Alice"},
        )
        # response.agent is the currently active agent
        # response.messages contains the full conversation
    """

    def __init__(self, provider: LLMProvider, default_model: str | None = None):
        self.provider = provider
        self.default_model = default_model or provider.get_default_model()

    async def run(
        self,
        agent: Agent,
        messages: list[dict[str, Any]],
        context_variables: dict | None = None,
        max_turns: int = 20,
    ) -> SwarmResponse:
        """Run the swarm loop.
        
        Args:
            agent: The initial (or current) agent.
            messages: Conversation history (will be shallow-copied).
            context_variables: Shared variables across agents.
            max_turns: Safety limit on LLM round-trips.
        
        Returns:
            SwarmResponse with updated messages, active agent, and context.
        """
        active_agent = agent
        ctx = deepcopy(context_variables or {})
        history = list(messages)  # shallow copy
        init_len = len(history)
        turns = 0

        while turns < max_turns:
            turns += 1

            # Build tool schemas from active agent's functions
            tools = [function_to_tool_schema(f) for f in active_agent.functions]
            # Hide context_variables from LLM
            for tool in tools:
                params = tool["function"]["parameters"]
                params["properties"].pop(_CTX_VARS_NAME, None)
                if _CTX_VARS_NAME in params.get("required", []):
                    params["required"].remove(_CTX_VARS_NAME)

            # Resolve instructions
            system_prompt = _get_instructions(active_agent, ctx)

            # Build messages: system prompt + history
            llm_messages = [{"role": "system", "content": system_prompt}] + history

            # Call LLM (with timeout to prevent hanging)
            model = active_agent.model or self.default_model
            try:
                response = await asyncio.wait_for(
                    self.provider.chat(
                        messages=llm_messages,
                        tools=tools if tools else None,
                        model=model,
                    ),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Swarm [{active_agent.name}] LLM call timed out after 120s"
                )
                history.append({
                    "role": "assistant",
                    "content": "抱歉，AI 响应超时了，请稍后再试。",
                })
                break

            # Add assistant message to history
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tool_call_dicts,
                }
                history.append(assistant_msg)

                # Execute tool calls
                for tc in response.tool_calls:
                    logger.debug(
                        f"Swarm [{active_agent.name}] executing: {tc.name}"
                    )
                    result = self._execute_function(
                        tc.name, tc.arguments, active_agent.functions, ctx
                    )

                    # Handle async functions
                    if inspect.isawaitable(result):
                        result = await result

                    parsed = self._handle_result(result)

                    # Tool result message
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": parsed.value,
                    })

                    # Merge context updates
                    ctx.update(parsed.context_variables)

                    # Handoff?
                    if parsed.agent:
                        logger.info(
                            f"Swarm handoff: {active_agent.name} → {parsed.agent.name}"
                        )
                        active_agent = parsed.agent
            else:
                # No tool calls → end turn
                history.append({
                    "role": "assistant",
                    "content": response.content or "",
                })
                break

        return SwarmResponse(
            messages=history[init_len:],
            agent=active_agent,
            context_variables=ctx,
        )

    def _execute_function(
        self,
        name: str,
        arguments: dict[str, Any],
        functions: list[Callable],
        context_variables: dict,
    ) -> Any:
        """Find and call the named function from the agent's function list."""
        func_map = {f.__name__: f for f in functions}
        func = func_map.get(name)
        if not func:
            return f"Error: function '{name}' not found"

        # Inject context_variables if the function accepts it
        sig = inspect.signature(func)
        if _CTX_VARS_NAME in sig.parameters:
            arguments[_CTX_VARS_NAME] = context_variables

        return func(**arguments)

    def _handle_result(self, raw: Any) -> SwarmResult:
        """Normalize a function return value into SwarmResult."""
        if isinstance(raw, SwarmResult):
            return raw
        if isinstance(raw, Agent):
            return SwarmResult(
                value=json.dumps({"handoff_to": raw.name}),
                agent=raw,
            )
        try:
            return SwarmResult(value=str(raw))
        except Exception:
            return SwarmResult(value="(function returned non-string result)")
