# Heartbeat Tasks

This file is checked every 24 hours by your nanobot agent.

## Active Tasks

- [ ] 检查 URPDF 用户反馈：调用 `curl http://localhost:8080/api/feedback/pending` 获取待处理反馈列表。如果列表不为空，将每条反馈的类型、内容、用户名、联系方式汇总后通知我。通知完成后，对每条反馈调用 `curl -X POST http://localhost:8080/api/feedback/{id}/resolve` 标记为已处理，避免下次重复发送。如果列表为空则跳过，不需要通知。

## Completed

<!-- Move completed tasks here or delete them -->

