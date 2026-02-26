"""Tests for the Swarm engine."""

import asyncio
import pytest

from nanobot.agent.swarm import Agent, Swarm, SwarmResult, function_to_tool_schema
from nanobot.agent.agents import (
    create_triage_agent,
    create_coder_agent,
    create_searcher_agent,
    handoff_to_coder,
    handoff_to_searcher,
    handoff_to_triage,
)


# ---------------------------------------------------------------------------
# Test Agent dataclass
# ---------------------------------------------------------------------------

class TestAgent:
    def test_default_agent(self):
        a = Agent(name="test")
        assert a.name == "test"
        assert a.instructions == "You are a helpful agent."
        assert a.functions == []
        assert a.model is None

    def test_callable_instructions(self):
        a = Agent(
            name="dynamic",
            instructions=lambda ctx: f"Hello {ctx.get('user', 'world')}",
        )
        text = a.instructions({"user": "Alice"})
        assert text == "Hello Alice"


# ---------------------------------------------------------------------------
# Test SwarmResult
# ---------------------------------------------------------------------------

class TestSwarmResult:
    def test_plain_value(self):
        r = SwarmResult(value="done")
        assert r.value == "done"
        assert r.agent is None

    def test_handoff(self):
        target = Agent(name="target")
        r = SwarmResult(value="handing off", agent=target)
        assert r.agent.name == "target"

    def test_context_update(self):
        r = SwarmResult(context_variables={"key": "val"})
        assert r.context_variables["key"] == "val"


# ---------------------------------------------------------------------------
# Test function_to_tool_schema
# ---------------------------------------------------------------------------

class TestFunctionToSchema:
    def test_basic_function(self):
        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"

        schema = function_to_tool_schema(greet)
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "greet"
        assert schema["function"]["description"] == "Say hello."
        props = schema["function"]["parameters"]["properties"]
        assert "name" in props
        assert props["name"]["type"] == "string"
        assert "name" in schema["function"]["parameters"]["required"]

    def test_context_variables_hidden(self):
        def my_func(task: str, context_variables: dict) -> str:
            """Do something."""
            return task

        schema = function_to_tool_schema(my_func)
        props = schema["function"]["parameters"]["properties"]
        assert "context_variables" not in props
        required = schema["function"]["parameters"]["required"]
        assert "context_variables" not in required

    def test_optional_param(self):
        def my_func(required_param: str, optional_param: str = "default") -> str:
            """Test."""
            return required_param

        schema = function_to_tool_schema(my_func)
        required = schema["function"]["parameters"]["required"]
        assert "required_param" in required
        assert "optional_param" not in required


# ---------------------------------------------------------------------------
# Test Swarm._handle_result
# ---------------------------------------------------------------------------

class TestHandleResult:
    def setup_method(self):
        # Swarm needs a provider but we only test _handle_result
        self.swarm = Swarm.__new__(Swarm)

    def test_string_result(self):
        r = self.swarm._handle_result("hello")
        assert isinstance(r, SwarmResult)
        assert r.value == "hello"
        assert r.agent is None

    def test_swarm_result(self):
        sr = SwarmResult(value="done", context_variables={"k": "v"})
        r = self.swarm._handle_result(sr)
        assert r is sr

    def test_agent_result(self):
        a = Agent(name="new_agent")
        r = self.swarm._handle_result(a)
        assert r.agent is a
        assert "new_agent" in r.value

    def test_numeric_result(self):
        r = self.swarm._handle_result(42)
        assert r.value == "42"


# ---------------------------------------------------------------------------
# Test handoff functions
# ---------------------------------------------------------------------------

class TestHandoffs:
    def test_handoff_to_coder(self):
        result = handoff_to_coder({})
        assert isinstance(result, SwarmResult)
        assert result.agent is not None
        assert result.agent.name == "coder"

    def test_handoff_to_searcher(self):
        result = handoff_to_searcher({})
        assert isinstance(result, SwarmResult)
        assert result.agent is not None
        assert result.agent.name == "searcher"

    def test_handoff_to_triage(self):
        result = handoff_to_triage({})
        assert isinstance(result, SwarmResult)
        assert result.agent is not None
        assert result.agent.name == "triage"


# ---------------------------------------------------------------------------
# Test agent factories
# ---------------------------------------------------------------------------

class TestAgentFactories:
    def test_triage_has_handoff_functions(self):
        agent = create_triage_agent()
        assert agent.name == "triage"
        func_names = [f.__name__ for f in agent.functions]
        assert "handoff_to_coder" in func_names
        assert "handoff_to_searcher" in func_names

    def test_coder_has_claude_code(self):
        agent = create_coder_agent()
        assert agent.name == "coder"
        func_names = [f.__name__ for f in agent.functions]
        assert "_run_claude_code" in func_names
        assert "handoff_to_triage" in func_names

    def test_searcher_has_handoff_back(self):
        agent = create_searcher_agent()
        assert agent.name == "searcher"
        func_names = [f.__name__ for f in agent.functions]
        assert "handoff_to_triage" in func_names

    def test_triage_dynamic_instructions(self):
        agent = create_triage_agent()
        # instructions should be callable
        assert callable(agent.instructions)
        text = agent.instructions({"channel": "feishu", "chat_id": "123"})
        assert "feishu" in text
        assert "123" in text
