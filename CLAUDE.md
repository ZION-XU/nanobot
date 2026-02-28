# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

nanobot is an ultra-lightweight (~4,000 LOC) personal AI assistant framework. It connects to chat platforms (Telegram, Discord, Slack, Feishu, WhatsApp, Email, QQ, DingTalk, Matrix, Mochat) and uses LLM providers to deliver agent functionality with tool use, memory, scheduled tasks, and MCP support.

## Common Commands

```bash
# Install from source (editable)
pip install -e .

# Run interactive CLI chat
nanobot agent

# Run chat gateway (connects to enabled chat channels)
nanobot gateway

# Initialize config & workspace
nanobot onboard

# Show status
nanobot status

# Lint
ruff check nanobot/

# Format
ruff format nanobot/

# Run tests
pytest tests/

# Count core agent lines
bash core_agent_lines.sh
```

## Lint & Format Configuration

- **Linter/Formatter**: `ruff` (line-length=100, target py311)
- **Lint rules**: E, F, I, N, W selected; E501 (line length) ignored
- **Tests**: `pytest` with `pytest-asyncio` (asyncio_mode = "auto")

## Architecture

### Message Flow

```
User Message → channels/<platform> → bus/ → session/ → agent/loop.py
    → providers/ (LLM call) → agent/tools/ (tool execution)
    → bus/ → channels/<platform> → User
```

### Key Modules

- **`agent/loop.py`** — Core agent loop: LLM ↔ tool execution cycle
- **`agent/context.py`** — System prompt builder (assembles persona, skills, tool descriptions)
- **`agent/memory.py`** — Short-term and long-term persistent memory
- **`agent/skills.py`** — Skills loader (reads `.md` files from `skills/` and `templates/`)
- **`agent/subagent.py`** — Background task execution (spawned sub-agents)
- **`agent/tools/`** — Built-in tools: shell, filesystem, web, cron, message, spawn, MCP bridge
- **`bus/`** — Internal message routing between agent and channels (event bus + queue)
- **`channels/`** — Chat platform integrations, each in its own file. All inherit from `channels/base.py`
- **`channels/manager.py`** — Starts/stops enabled channel drivers
- **`providers/`** — LLM provider abstraction layer over `litellm`
- **`providers/registry.py`** — Provider registry (single source of truth for all provider specs)
- **`providers/custom_provider.py`** — Direct OpenAI-compatible endpoint (bypasses litellm)
- **`session/manager.py`** — Conversation state management per user/channel
- **`config/schema.py`** — Pydantic config schema; `config/loader.py` loads `~/.nanobot/config.json`
- **`cron/service.py`** — Scheduled task runner (croniter-based)
- **`heartbeat/service.py`** — Periodic wake-up daemon (checks `HEARTBEAT.md` every 30 min)
- **`cli/commands.py`** — Typer CLI entry point

### Adding a New LLM Provider (2 steps)

1. Add a `ProviderSpec` entry to `PROVIDERS` list in `nanobot/providers/registry.py`
2. Add a field to `ProvidersConfig` in `nanobot/config/schema.py`

Everything else (env vars, model prefixing, status display) is automatic.

### Adding a New Channel

Each channel is a single file in `channels/` inheriting from `channels/base.py`. Register it in `channels/manager.py`.

### Tool System

Tools are defined in `agent/tools/` with a registry pattern (`agent/tools/registry.py`). MCP tools are bridged via `agent/tools/mcp.py` and auto-discovered at startup from `~/.nanobot/config.json` `tools.mcpServers`.

### Prompts & Skills

System prompts are assembled from markdown templates in `nanobot/templates/` and `nanobot/skills/`. Skills are `.md` files that get injected into the system prompt, not hard-coded in Python.

## Configuration

User config lives at `~/.nanobot/config.json`. Schema is defined in `nanobot/config/schema.py`. Key sections: `providers`, `agents`, `channels`, `tools`.

Workspace directory: `~/.nanobot/workspace/`

## Dependencies

- Python >= 3.11, build system: `hatchling`
- Core: `typer`, `litellm`, `pydantic`, `httpx`, `websockets`, `loguru`, `rich`, `mcp`
- Channel SDKs: `python-telegram-bot`, `slack-sdk`, `lark-oapi`, `qq-botpy`, etc.
- Optional: `matrix-nio[e2e]` (install with `pip install nanobot-ai[matrix]`)
