---
feature_ids: [F200]
related_features: []
topics: [autonomous-chat, multi-cat, orchestration, A2A]
doc_kind: spec
created: 2026-04-05
---

# F200: 多猫自驱对话编排器

> Status: spec | Owner: 宪宪 (opus-45)

## Why

CatChat 目前有 A2A @mention 协议、MCP 回调系统和多猫聊天模式，但缺少 clowder-ai 核心的**串行工作列表编排器**。猫猫之间的对话是手动触发的，无法自动链式触发下一个猫，也无法实时看到猫猫的意图状态。

## What

在 CatChat 的 Node.js 回调服务器 (`callback-server.js`) 和 MCP server (`cat_cafe_mcp.js`) 基础上，添加：

### P0 — 核心编排
- **ConversationOrchestrator**: 串行工作列表循环，猫猫按 @mention 链式触发，最深 8 层
- **CatIntentionReporter**: 猫猫上报意图（analyzing/implementing/waiting/blocked）+ WebSocket 广播
- **MCP 工具扩展**: `report_intention` / `clear_intention` / `create_decision` / `list_decisions` / `check_context`

### P1 — 增强能力
- **Decision Log**: 决策日志，支持哈希链不可篡改记录
- **猫猫意图 Sidebar**: 前端实时显示所有在线猫猫的意图状态
- **意图气泡 UI**: 各猫猫当前状态的可视化（参考 clowder-ai IntentionSidebar）

### P2 — 进阶能力
- **并行讨论模式**: 一个猫发起，多猫同时回复
- **茶话会模式**: 自由讨论，自动 @mention 触发下一只猫

## Architecture

```
User Message
    ↓
backend/app/api/messages.py (消息入口)
    ↓
ConversationOrchestrator (新增: backend/orchestrator/)
    ├── TargetResolver (复用现有 a2a_parser 逻辑)
    ├── A2AParser (复用: docs/autonomous-cat-conversation-architecture.md)
    ├── AgentRunner (复用现有 cat_chat_cli.js)
    └── RealtimePublisher (复用现有 WebSocket)
    ↓
Cat Agent (via CLI/回调)
    ↓
Cat 回复 → @mention → 下一个猫
    ↓
意图上报 (report_intention MCP tool)
```

### 核心文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/orchestrator/conversation.py` | 新增 | ConversationOrchestrator 核心 |
| `backend/app/orchestrator/intention.py` | 新增 | 意图状态管理 + WebSocket 广播 |
| `backend/app/orchestrator/decision_log.py` | 新增 | 决策日志 MVP |
| `backend/app/api/autonomous_chat.py` | 新增 | REST API 入口 |
| `backend/app/schemas.py` | 修改 | 添加编排相关 Pydantic 模型 |
| `callback-server.js` | 修改 | 注册意图/决策日志路由 |
| `cat_cafe_mcp.js` | 修改 | 添加 MCP 工具 |
| `cat_chat.html` | 修改 | 意图气泡 Sidebar UI |

### 数据流 (Serial Handoff)

```python
worklist = [initial_cat]

while worklist and depth < 8:
    cat_id = worklist.pop(0)
    depth += 1

    # 广播意图
    broadcast_intention(cat_id, "analyzing", "正在思考...")

    result = await invoke_cat(cat_id, context)

    # 广播意图完成
    broadcast_intention_done(cat_id)

    # 持久化
    store.append(role="agent", agent_id=cat_id, content=result)

    # 解析下一跳
    next_cats = parse_a2a_mentions(result, cat_id)
    worklist.extend(next_cats)
```

## Acceptance Criteria

- [ ] AC-1: 用户发消息 → 编排器自动触发目标猫猫（按 @mention 或默认路由）
- [ ] AC-2: 猫猫回复中的 `行首 @mention` 自动触发下一只猫
- [ ] AC-3: 每只猫的上报意图实时广播到前端（analyzing → implementing → waiting → 完成）
- [ ] AC-4: Decision Log MCP 工具可用（创建/确认/查询）
- [ ] AC-5: 意图气泡 Sidebar 实时显示所有猫猫状态
- [ ] AC-6: 链式对话最深 8 层自动截断
- [ ] AC-7: 猫猫掉线/超时有 fallback 和错误处理

## Dependencies

- 现有 `a2a_parser.py` (复用)
- 现有 `cat_chat_cli.js` Agent Runner (复用)
- 现有 WebSocket 广播系统 (复用)
- clowder-ai 的 `callback-server.js` 模式 (参考)

## Risk

- CatChat 是 Python backend + JS frontend + Node.js agent runner，技术栈混合，增加编排复杂度
- 需要确保 cat_chat_cli.js 能正确处理异步调用和流式输出
- 多猫并发时需要队列保护，避免状态竞争

## Open Questions

1. 意图上报的触发时机：每个 cat 响应前/中/后都需要上报吗？参考 clowder-ai，只在"开始新任务"和"被阻塞"时触发
2. Decision Log 存储：MVP 用内存 Map 还是复用 CatChat 现有的 SQLite 表？
3. 前端意图 Sidebar 的渲染位置：固定右下角还是作为独立面板？
