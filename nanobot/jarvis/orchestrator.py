"""Orchestrator — plans, dispatches, and merges multi-agent tasks."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.jarvis.memory import SharedMemory
from nanobot.jarvis.pool import AgentPool, AgentResult
from nanobot.jarvis.roles import RoleRegistry, RoleSpec


class Orchestrator:
    """
    Top-level swarm orchestrator.

    1. Plan  — LLM breaks user request into a task DAG
    2. Dispatch — executes tasks respecting dependencies, parallelizing where possible
    3. Merge — collects results and produces a final report
    """

    def __init__(
        self,
        provider: Any,  # LLMProvider
        workspace: Path,
        role_registry: RoleRegistry,
        agent_pool: AgentPool,
        model: str | None = None,
        max_tokens: int = 8192,
    ):
        self.provider = provider
        self.workspace = workspace
        self.roles = role_registry
        self.pool = agent_pool
        self.model = model or provider.get_default_model()
        self.max_tokens = max_tokens

    async def handle(
        self,
        user_request: str,
        session_id: str | None = None,
        on_progress: Any = None,
    ) -> str:
        """End-to-end orchestration: plan → dispatch → merge."""
        session_id = session_id or uuid.uuid4().hex[:12]
        shared_mem = SharedMemory(session_id)

        logger.info("Swarm session {} started: {}", session_id, user_request[:80])

        # Phase 1: Plan
        if on_progress:
            await on_progress("Planning task breakdown...")
        task_graph = await self._plan(user_request)
        shared_mem.write_task_graph(task_graph)

        if not task_graph:
            return "I couldn't break this request into actionable tasks. Please try rephrasing."

        logger.info("Task graph: {} tasks", len(task_graph))

        # Phase 2: Dispatch
        results = await self._dispatch(task_graph, shared_mem, on_progress)

        # Phase 3: Merge
        if on_progress:
            await on_progress("Synthesizing results...")
        final_report = await self._merge(user_request, task_graph, results)
        shared_mem.write_final_report(final_report)

        logger.info("Swarm session {} completed", session_id)
        return final_report

    async def _plan(self, user_request: str) -> dict[str, dict[str, Any]]:
        """Use LLM to decompose the request into a task graph."""
        available_roles = self.roles.all()
        roles_desc = "\n".join(
            f"- **{r.name}**: {r.display_name}"
            + (f" (uses Claude Code for implementation)" if r.use_claude_code else "")
            for r in available_roles.values()
        )

        prompt = f"""You are a task planner for an AI agent swarm. Break the user's request into a task graph.

## Available Agent Roles
{roles_desc}

## Rules
1. Each task must specify a `role` (one of the available roles above)
2. Tasks can depend on other tasks via `depends` (list of task IDs)
3. Tasks without dependencies can run in parallel
4. Keep it minimal — don't create unnecessary tasks
5. For coding/implementation work, always use the `coder` role
6. For research/information gathering, use the `researcher` role
7. For testing/review, use the `qa` role
8. For documentation/writing, use the `writer` role
9. If the task is simple enough for a single agent, just create ONE task

## Output Format
Return ONLY valid JSON (no markdown fences, no explanation):
{{
  "t1": {{"role": "researcher", "task": "description of what to do", "depends": []}},
  "t2": {{"role": "coder", "task": "description of what to do", "depends": ["t1"]}}
}}

## User Request
{user_request}"""

        messages = [
            {"role": "system", "content": "You are a task planning assistant. Output valid JSON only."},
            {"role": "user", "content": prompt},
        ]

        response = await self.provider.chat(
            messages=messages,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.1,
        )

        return self._parse_task_graph(response.content or "")

    @staticmethod
    def _parse_task_graph(content: str) -> dict[str, dict[str, Any]]:
        """Parse LLM output into a task graph dict."""
        import json_repair
        text = content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            graph = json_repair.loads(text)
        except Exception:
            logger.error("Failed to parse task graph: {}", text[:200])
            return {}

        if not isinstance(graph, dict):
            return {}

        # Validate structure
        valid = {}
        for tid, task in graph.items():
            if isinstance(task, dict) and "role" in task and "task" in task:
                task.setdefault("depends", [])
                valid[tid] = task

        return valid

    async def _dispatch(
        self,
        task_graph: dict[str, dict[str, Any]],
        shared_mem: SharedMemory,
        on_progress: Any = None,
    ) -> dict[str, AgentResult]:
        """Execute the task graph respecting dependency order."""
        completed: dict[str, AgentResult] = {}
        pending = set(task_graph.keys())

        while pending:
            # Find ready tasks (all dependencies met)
            ready = []
            for tid in list(pending):
                deps = task_graph[tid].get("depends", [])
                if all(d in completed for d in deps):
                    ready.append(tid)

            if not ready:
                logger.error("Deadlock in task graph! Remaining: {}", pending)
                break

            # Launch ready tasks in parallel
            if on_progress:
                role_names = [task_graph[tid]["role"] for tid in ready]
                await on_progress(f"Running: {', '.join(role_names)}...")

            coros = []
            for tid in ready:
                task_spec = task_graph[tid]
                role = self.roles.get(task_spec["role"])
                if not role:
                    logger.warning("Unknown role '{}' for task {}, skipping", task_spec["role"], tid)
                    completed[tid] = AgentResult(
                        role=task_spec["role"], task_id=tid, task=task_spec["task"],
                        output=f"Error: Unknown role '{task_spec['role']}'", status="error",
                    )
                    pending.discard(tid)
                    continue

                coros.append((tid, self.pool.run_agent(role, tid, task_spec["task"], shared_mem)))

            if coros:
                tasks = await asyncio.gather(
                    *(coro for _, coro in coros), return_exceptions=True,
                )
                for (tid, _), result in zip(coros, tasks):
                    if isinstance(result, Exception):
                        completed[tid] = AgentResult(
                            role=task_graph[tid]["role"], task_id=tid,
                            task=task_graph[tid]["task"],
                            output=f"Error: {result}", status="error",
                        )
                    else:
                        completed[tid] = result
                    pending.discard(tid)

                    status = "completed" if not isinstance(result, Exception) and result.status == "ok" else "failed"
                    logger.info("Task {} ({}): {}", tid, task_graph[tid]["role"], status)

        return completed

    async def _merge(
        self,
        user_request: str,
        task_graph: dict[str, dict[str, Any]],
        results: dict[str, AgentResult],
    ) -> str:
        """Merge all agent outputs into a final report."""
        # If only one task, return its output directly
        if len(results) == 1:
            result = next(iter(results.values()))
            return result.output

        # Build summary of all results
        results_text = []
        for tid in sorted(results.keys()):
            r = results[tid]
            status_icon = "ok" if r.status == "ok" else "FAILED"
            results_text.append(
                f"### Task {tid} [{r.role}] — {status_icon} ({r.duration_s:.1f}s)\n"
                f"**Assignment:** {r.task}\n\n"
                f"**Output:**\n{r.output}\n"
            )

        prompt = f"""You are synthesizing results from multiple AI agents into a final response for the user.

## Original User Request
{user_request}

## Agent Results
{"---".join(results_text)}

## Instructions
1. Combine the agent outputs into a coherent, unified response
2. Highlight key findings, actions taken, and any issues
3. Be concise — remove redundancy across agents
4. If any agent failed, mention it and explain the impact
5. Format the response in clear markdown"""

        messages = [
            {"role": "system", "content": "You are a results synthesizer. Produce a clear, unified report."},
            {"role": "user", "content": prompt},
        ]

        response = await self.provider.chat(
            messages=messages,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.1,
        )

        return response.content or "(Failed to synthesize results)"
