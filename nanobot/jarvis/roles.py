"""Role registry — loads agent role definitions from YAML files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class RoleSpec:
    """Specification for a single agent role."""

    name: str
    display_name: str
    model: str | None = None  # None = inherit from nanobot config
    max_iterations: int = 20
    tools: list[str] = field(default_factory=list)
    can_delegate_to: list[str] = field(default_factory=list)
    prompt_file: str = ""
    use_claude_code: bool = False  # If True, delegate to `claude` CLI instead of LLM loop
    system_prompt: str = ""  # Loaded from prompt_file at init time

    def load_prompt(self, roles_dir: Path) -> None:
        """Load the companion .md prompt file."""
        if not self.prompt_file:
            return
        md_path = roles_dir / self.prompt_file
        if md_path.exists():
            self.system_prompt = md_path.read_text(encoding="utf-8")
        else:
            logger.warning("Prompt file not found: {}", md_path)


class RoleRegistry:
    """Registry of available agent roles."""

    def __init__(self, roles_dir: Path | None = None):
        self._roles: dict[str, RoleSpec] = {}
        self._roles_dir = roles_dir or (Path(__file__).parent / "roles")
        self._load_builtin_roles()

    def _load_builtin_roles(self) -> None:
        """Load built-in role definitions from the roles/ directory."""
        if not self._roles_dir.exists():
            logger.warning("Roles directory not found: {}", self._roles_dir)
            return

        for yaml_file in sorted(self._roles_dir.glob("*.yaml")):
            try:
                role = self._parse_yaml(yaml_file)
                role.load_prompt(self._roles_dir)
                self._roles[role.name] = role
                logger.debug("Loaded role: {}", role.name)
            except Exception as e:
                logger.error("Failed to load role {}: {}", yaml_file.name, e)

    @staticmethod
    def _parse_yaml(path: Path) -> RoleSpec:
        """Parse a YAML role file. Uses a minimal parser to avoid PyYAML dependency."""
        text = path.read_text(encoding="utf-8")
        data: dict[str, Any] = {}
        current_list_key: str | None = None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                current_list_key = None
                continue

            # List item continuation
            if stripped.startswith("- ") and current_list_key:
                data.setdefault(current_list_key, []).append(stripped[2:].strip())
                continue

            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()

                if not value:
                    current_list_key = key
                    continue

                current_list_key = None

                # Type coercion
                if value.lower() in ("true", "false"):
                    data[key] = value.lower() == "true"
                elif value.isdigit():
                    data[key] = int(value)
                else:
                    data[key] = value.strip("\"'")
            else:
                current_list_key = None

        # Map to RoleSpec
        return RoleSpec(
            name=data.get("name", path.stem),
            display_name=data.get("display_name", path.stem),
            model=data.get("model"),
            max_iterations=data.get("max_iterations", 20),
            tools=data.get("tools", []),
            can_delegate_to=data.get("can_delegate_to", []),
            prompt_file=data.get("prompt", ""),
            use_claude_code=data.get("use_claude_code", False),
        )

    def get(self, name: str) -> RoleSpec | None:
        return self._roles.get(name)

    def list_roles(self) -> list[str]:
        return list(self._roles.keys())

    def all(self) -> dict[str, RoleSpec]:
        return dict(self._roles)
