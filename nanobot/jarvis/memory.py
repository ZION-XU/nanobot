"""Shared memory for agent swarm — file-based workspace all agents can read/write."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class SharedMemory:
    """
    File-based shared memory for a swarm session.

    Each session gets a directory under ~/.nanobot/swarm/<session_id>/.
    Agents write findings as markdown files; the orchestrator reads them
    to build context for downstream agents.
    """

    def __init__(self, session_id: str, base_dir: Path | None = None):
        self.session_id = session_id
        self._base = base_dir or (Path.home() / ".nanobot" / "swarm")
        self.workspace = self._base / session_id
        self.workspace.mkdir(parents=True, exist_ok=True)

    def write(self, agent_name: str, key: str, content: str) -> Path:
        """Write an artifact from an agent. Returns the file path."""
        filename = f"{agent_name}.{key}.md"
        path = self.workspace / filename
        path.write_text(content, encoding="utf-8")
        logger.debug("SharedMemory: {} wrote {}", agent_name, filename)
        return path

    def read(self, agent_name: str, key: str) -> str | None:
        """Read a specific artifact."""
        path = self.workspace / f"{agent_name}.{key}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def read_all(self) -> dict[str, str]:
        """Read all artifacts in this session."""
        results = {}
        for f in sorted(self.workspace.glob("*.md")):
            results[f.stem] = f.read_text(encoding="utf-8")
        return results

    def get_briefing(self) -> str:
        """Generate a briefing of all current findings for agent context injection."""
        artifacts = self.read_all()
        if not artifacts:
            return "(No prior findings yet.)"

        parts = ["# Prior findings from other agents\n"]
        for name, content in artifacts.items():
            parts.append(f"## {name}\n{content}\n")
        return "\n".join(parts)

    def write_task_graph(self, task_graph: dict[str, Any]) -> None:
        """Persist the task graph for debugging/inspection."""
        path = self.workspace / "_task_graph.json"
        path.write_text(json.dumps(task_graph, indent=2, ensure_ascii=False), encoding="utf-8")

    def write_final_report(self, report: str) -> None:
        """Write the final merged report."""
        path = self.workspace / "_final_report.md"
        path.write_text(report, encoding="utf-8")

    def cleanup(self) -> None:
        """Remove the session workspace."""
        import shutil
        if self.workspace.exists():
            shutil.rmtree(self.workspace)
