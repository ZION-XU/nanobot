---
name: claude-code
description: "使用 Claude Code 进行代码编写。支持进入 Claude Code 模式进行多轮对话。"
metadata: {"nanobot":{"emoji":"🤖","requires":{"bins":["claude","python"]}}}
---

# Claude Code Skill

使用 Claude Code CLI 帮助用户完成代码任务。通过本地会话管理器实现多轮对话。

## 会话管理器位置

```
~/.nanobot/workspace/skills/claude-code/cc.py
```

## 进入 Claude Code 模式

当用户想要在某个项目中进行代码开发时：

```bash
python ~/.nanobot/workspace/skills/claude-code/cc.py enter "<工作目录>"
```

示例：
```bash
python ~/.nanobot/workspace/skills/claude-code/cc.py enter "G:\projects\myapp"
```

返回 JSON：
```json
{
  "session_id": "abc123def456",
  "working_dir": "G:\\projects\\myapp",
  "message": "✅ 已进入 Claude Code 模式..."
}
```

**重要**：记住返回的 `session_id`，后续所有操作都需要它。

## 发送消息给 Claude Code

进入模式后，用户的每条消息都应该用 send 命令：

```bash
python ~/.nanobot/workspace/skills/claude-code/cc.py send "<session_id>" "<用户消息>"
```

示例：
```bash
python ~/.nanobot/workspace/skills/claude-code/cc.py send "abc123def456" "分析这个项目的结构"
```

返回 JSON：
```json
{
  "session_id": "abc123def456",
  "output": "这个项目是一个..."
}
```

## 退出 Claude Code 模式

当用户说"退出"、"exit"等：

```bash
python ~/.nanobot/workspace/skills/claude-code/cc.py exit "<session_id>"
```

## 检查会话状态

```bash
python ~/.nanobot/workspace/skills/claude-code/cc.py status "<session_id>"
```

## 使用流程

1. 用户说"进入 G:\projects\myapp 写代码"
   → 调用 `cc.py enter "G:\projects\myapp"`
   → 保存返回的 session_id 到对话上下文

2. 用户发送后续消息
   → 调用 `cc.py send "<session_id>" "<消息>"`
   → 将 output 返回给用户

3. 用户说"退出"
   → 调用 `cc.py exit "<session_id>"`
   → 清除 session_id，回到正常对话

## 识别进入模式的关键词

- "进入 xxx 写代码"
- "打开 xxx 编程"
- "claude code xxx"
- "在 xxx 目录写代码"

## 识别退出模式的关键词

- "退出"
- "exit"
- "quit"
- "退出 claude"

## 注意事项

- session_id 必须在对话中持续追踪
- 所有命令返回 JSON 格式，方便解析
- 无超时限制，可执行长时间任务
