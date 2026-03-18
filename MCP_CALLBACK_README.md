# MCP 回传系统 - 最小实现

这个系统演示了 AI Agent 如何通过 MCP 工具主动与外部系统通信的机制。

## 架构概览

```
┌─────────────┐         ┌──────────────┐         ┌─────────────────┐
│  Claude CLI │ ──────► │  MCP Server  │ ──────► │ Callback Server │
│  (AI Agent) │  stdio  │ (cat-cafe)   │  HTTP   │  (聊天室模拟)   │
└─────────────┘         └──────────────┘         └─────────────────┘
       │                       │                         │
       │  内心独白             │  工具调用               │  公开消息
       │  (不可见)             │  (AI 自主选择)           │  (可见)
       ▼                       ▼                         ▼
```

## 核心概念

### 1. AI 的"内心独白" vs "公开演讲"

- **内心独白**: Claude CLI 的标准输出，包含完整的思考过程，默认只有开发者能看到
- **公开演讲**: AI 通过 MCP 工具主动调用 HTTP callback，选择性地把某些消息发送到聊天室

### 2. 为什么需要 MCP？

MCP (Model Context Protocol) 让 AI 能够：
- 访问外部工具和资源
- **自主选择**何时、何地、发送什么信息
- 保持与外部系统的解耦

### 3. 认证机制

使用 `invocationId` + `callbackToken` 双重验证：
- 防止未授权的消息注入
- 每个会话有独立的凭证对
- 类似 API Key + Secret 的模式

## 快速开始

### 步骤 1: 安装依赖

```bash
npm init -y
npm install @modelcontextprotocol/sdk zod
```

### 步骤 2: 启动回调服务器（终端 1）

```powershell
node callback-server.js
```

启动后会显示生成的凭证：
```
=== CatChat Callback Server ===
Invocation ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Callback Token: yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
```

**记录这两个 UUID！**

### 步骤 3: 运行 AI Agent（终端 2）

```powershell
node run-cat.js
```

按提示输入刚才记录的凭证，然后 AI 会：
1. 调用 `cat_cafe_get_context()` 获取上下文
2. 创作诗歌
3. 调用 `cat_cafe_post_message()` 发送最终作品

### ⚠️ 首次使用前必须登录

在运行任何命令之前，确保已经登录 Claude CLI:

```powershell
# 检查是否已安装
claude --version

# 如果没有安装，先安装
npm install -g @anthropic-ai/claude-code

# 登录（会打开浏览器进行 OAuth 认证）
claude login
```

登录后会显示 `✓ Logged in as xxxxx`，然后再继续下面的步骤。

### 方式 2: 直接运行（跳过 run-cat.js）

如果你已经有了凭证，可以直接运行：

```powershell
$env:CAT_CAFE_API_URL="http://localhost:3200"
$env:CAT_CAFE_INVOCATION_ID="你的-invocation-id"
$env:CAT_CAFE_CALLBACK_TOKEN="你的-callback-token"

claude -p "你的任务是写一首关于猫的诗。先用 cat_cafe_get_context 获取上下文，然后用 cat_cafe_post_message 把诗发到聊天室。" `
  --output-format stream-json `
  --verbose `
  --mcp-config '{\"mcpServers\":{\"cat-cafe\":{\"command\":\"node\",\"args\":[\"cat-cafe-mcp.js\"]}}}'
```

## 观察现象

### 在终端 1（Callback Server）你会看到：

```
[200] Thread context requested
=== 📬 Message Posted to Chat Room ===
Content: 猫咪轻步走过午后的阳光
尾巴摇摆着时间的节奏
...
======================================
```

这模拟了"消息出现在聊天室"的效果。

### 在终端 2（Claude CLI）你会看到：

AI 的完整思考过程，包括：
- 如何理解任务
- 创作的中间状态
- **决定调用哪个 MCP 工具** ← 这就是"自主权"

## 关键学习点

### 1. 信息分离

```
AI 的思考 (stdout)          AI 的选择 (MCP callback)
────────────────────        ────────────────────────
"我需要写一首诗..."    ──►   [不调用，这是内心活动]
"猫的特点是优雅..."   ──►   [不调用，这是草稿]
"好了，完成！"        ──►   cat_cafe_post_message("诗的内容")
```

### 2. 自主权的体现

AI **可以选择不说**：
- 不是所有思考都要公开
- AI 判断什么值得分享
- 类似人类的"三思而后言"

### 3. 协议设计

Callback 的设计要点：
- **双向验证**: invocationId + callbackToken
- **幂等性**: 同一消息可重复提交（根据业务需求）
- **异步解耦**: AI 不等待聊天室处理结果

## 扩展实验

### 实验 1: 修改过滤规则

在 `cat-cafe-mcp.js` 中添加内容审查：

    isError: true
  };
}
```

在 `callback-server.js` 中添加消息数组：

```javascript
const messageHistory = [];

// 在 POST handler 中
messageHistory.push({ timestamp: Date.now(), content });
```

然后在 `cat_cafe_get_context` 中返回真实的历史记录。

### 实验 3: 多房间支持

扩展 API 支持不同聊天室：

```javascript
POST /api/callbacks/post-message
{
  "roomId": "general",  // 或 "private-room-123"
  "content": "..."
}
```

## 故障排查

### 问题：`claude` 命令不存在

确保已安装 Claude CLI：
```bash
npm install -g @anthropic-ai/claude-code
```

### 问题：`Not logged in · Please run /login`

运行登录命令：
```bash
claude login
```

按照提示在浏览器中完成 OAuth 认证。

### 问题：MCP Server 无法启动

检查 Node.js 版本（需要 18+）：
```bash
node --version
```

检查依赖是否安装：
```bash
npm list @modelcontextprotocol/sdk zod
```

### 问题：401 Unauthorized

确认：
1. callback-server.js 输出的 UUID 正确复制
2. 环境变量设置正确
3. 没有多余的空格或引号

## 下一步

理解了这个最小系统后，可以尝试：

1. **集成到真实聊天室**: 替换 `console.log` 为 WebSocket 或数据库写入
2. **添加权限系统**: 不同 AI 有不同的发言权限
3. **实现消息队列**: 支持高并发场景
4. **添加审计日志**: 记录所有 AI 的发言历史

## 相关文件

- [`callback-server.js`](callback-server.js) - HTTP 回调服务器
- [`cat-cafe-mcp.js`](cat-cafe-mcp.js) - MCP Server 实现
- [`run-cat.js`](run-cat.js) - 便捷的启动脚本
