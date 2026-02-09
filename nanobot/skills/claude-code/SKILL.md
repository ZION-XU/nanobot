---
name: claude-code
description: "使用 Claude Code 进行代码编写。支持进入 Claude Code 模式，直接与 Claude Code 对话。"
metadata: {"nanobot":{"emoji":"🤖","requires":{"bins":["claude"]}}}
---

# Claude Code Skill

使用 Claude Code CLI 帮助用户完成代码任务。

## 进入 Claude Code 模式

当用户想要在某个项目中进行代码开发时，可以进入 Claude Code 模式。

用户说法示例：
- "进入 G:\projects\myapp 写代码"
- "打开 /home/user/project 编程"
- "claude code G:\0ai\nanobot"
- "在 D:\work\api 写代码"

进入后：
- 用户的所有消息会直接发送给 Claude Code
- Claude Code 的输出会直接返回给用户
- 无超时限制，可以执行长时间任务
- 自动保持会话上下文

退出方式：
- 发送「退出」或「exit」

## 单次调用（不进入模式）

对于简单的一次性任务，也可以直接用 exec 调用：

```bash
cd "<目录>" && claude --print --dangerously-skip-permissions "<任务>"
```

## 模式对比

| 特性 | Claude Code 模式 | 单次调用 |
|------|-----------------|---------|
| 超时 | 无限制 | 受 exec timeout 限制 |
| 上下文 | 保持多轮对话 | 每次独立 |
| 交互 | 直接透传 | 经过 LLM |
| 适用 | 复杂开发任务 | 简单单次任务 |
