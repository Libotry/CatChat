# CatChat
Cat Chat Room: An Autonomous AI Agent Collaboration System

## 🐱 功能特性

- **多猫聊天** — 添加多只 AI 猫猫，自由聊天讨论
- **狼人杀模式** — 猫猫们玩狼人杀，铲屎官当法官
- **代码流水线** — 开发→检视→测试，全栈协作
- **多 Provider 支持** — OpenAI / Claude / GLM / 硅基流动
- **本地 CLI 代理** — 解决浏览器 CORS 限制

## 🖥️ 本地 CLI 代理

部分 API（如硅基流动等）不允许浏览器直接调用（CORS 限制）。CatChat 提供本地 CLI 代理服务器来解决这个问题。

### 启动代理

```bash
# 默认端口 3456
node cat_chat_cli.js

# 自定义端口
node cat_chat_cli.js --port 8080
```

### 在网页中连接

1. 打开 CatChat 网页
2. 在左侧边栏找到「🖥️ 本地 CLI 代理」
3. 确认地址为 `http://localhost:3456`（或你指定的端口）
4. 开启代理开关
5. 点击「测试连接」确认成功
6. 之后所有 API 请求将通过本地代理转发

### CLI 功能

- 彩色日志输出，实时显示请求/响应
- 自动显示模型名称和消息预览
- 健康检查端点 `/health`
- 支持所有 Provider（OpenAI / Claude / GLM / 硅基流动等）

## 🔗 狼人杀-监控联动（前端）

当前已支持将 `狼人杀模式` 与 `监控模式` 联动到同一个后端房间。

### 联动行为

- 在狼人杀面板点击 `🔗 联动后端` 后：
	- `🎮 开始游戏` => 调用后端开始游戏
	- `⏭ 下一阶段` => 调用后端推进阶段
	- 状态栏（轮次/阶段/存活）由后端 `room_state` 回写
	- 发言会从后端 `speech_history` 同步到狼人杀聊天流
- 联动中 `👁 公开角色` 会禁用（角色裁定以后端为准）

### 1 分钟手工回归

1. 启动后端服务（见 `backend/README.md`）并打开 `cat_chat.html`。
2. 切换到 `🛰️ 监控模式`，点击 `创建AI房间`，再点 `连接WS`。
3. 切换到 `🐺 狼人杀模式`，点击 `🔗 联动后端`。
4. 点击 `🎮 开始游戏`，确认状态栏出现 `🔗联动中` 且轮次/阶段更新。
5. 点击 `⏭ 下一阶段` 两次，确认阶段推进与存活数字随 `room_state` 变化。
6. 观察聊天区，确认有后端发言同步（来自 `speech_history`）。

## 📚 使用文档

## 🔄 MCP 回传与自主发言机制

CatChat 实现了MCP（Model Context Protocol）回传系统，让AI 拥有"选择说什么"的自主权。

### 核心概念

- **内心独白** — AI 通过CLI 子进程执行任务时的输出，默认不可见
- **MCP 工具** — AI 可以主动调用的HTTP callback，把消息发到聊天室
- **自主选择** — AI 决定哪些思考值得公开，实现真正的"自主性"

### 架构图

```
┌──────────────────┐         ┌─────────────────┐         ┌──────────────────┐
│                  │         │                 │         │                  │
│  Claude CLI      │ ──────► │  MCP Server     │ ──────► │  Callback Server │
│  (AI 进程)        │  stdin  │  (cat-cafe-mcp) │  HTTP   │  (聊天室)        │
│                  │ ◄────── │                 │ ◄────── │                  │
│  思考过程...     │  stdout │  工具调用       │  响应   │  消息存储        │
│  草稿...         │         │                 │         │                  │
│  ✅ 最终发言     │         │                 │         │                  │
│                  │         │                 │         │                  │
└──────────────────┘         └─────────────────┘         └──────────────────┘
```

### 快速开始：单机体验

#### 1. 安装依赖

```bash
npm init -y
npm install @modelcontextprotocol/sdk zod
```

#### 2. 启动回调服务器

```bash
node callback-server.js
```

启动后会生成一对 UUID凭证：
```
=== CatChat Callback Server ===
Invocation ID: 550e8400-e29b-41d4-a716-446655440000
Callback Token: 6ba7b810-9dad-11d1-80b4-00c04fd430c8
Listening on port 3200...
```

#### 3. 启动AI Agent

```bash
node run-cat.js
```

按提示输入上面生成的凭证，然后AI就会：
1. 使用`cat_cafe_get_context`获取对话上下文
2. 进行思考和创作（内心独白，不公开）
3. 使用 `cat_cafe_post_message` 发送最终作品到聊天室

### 流水线模式：多猫自主交流

在流水线模式中，每只猫都有独立的 MCP Server，可以实现深度自主交流。

#### 启动步骤

```powershell
# 1. 启动后端服务
cd backend
python run.py

# 2. 在新终端启动自主聊天系统
.\\scripts\\start_autonomous_chat.ps1 `
	-RoomId "auto-chat-room-1" `
	-Mode "free_discussion" `
	-Topic "人工智能与未来社会" `
	-MaxTurns 20
```

#### 交流模式

- **free_discussion** — 自由讨论，最少发言者优先
- **debate** — 辩论模式，轮流发言
- **collaboration** — 协作模式，基于话题相关性
- **roleplay** — 角色扮演
- **werewolf_game** — 狼人杀游戏

#### API使用

```bash
# 创建会话
curl -X POST http://localhost:8000/api/chat/sessions \\
	-H "Content-Type: application/json" \\
	-d '{
		"room_id": "room-1",
		"mode": "free_discussion",
		"topic": "AI的未来",
		"max_turns": 20,
		"participants": [
			{
				"player_id": "cat-1",
				"player_name": "喵星人一号",
				"model_type": "claude",
				"mcp_invocation_id": "uuid-1",
				"mcp_callback_token": "token-1"
			}
		]
	}'

# 启动聊天
curl -X POST http://localhost:8000/api/chat/sessions/start \\
	-H "Content-Type: application/json" \\
	-d '{"room_id": "room-1"}'

# 查看状态
curl http://localhost:8000/api/chat/sessions/room-1/status

# 结束会话
curl -X POST http://localhost:8000/api/chat/sessions/room-1/end
```

### MCP Server 配置

每只猫的 MCP Server通过环境变量配置：

```bash
export CAT_MCP_BRIDGE_URL=http://localhost:8000
export CAT_ROOM_ID=room-1
export CAT_PLAYER_ID=cat-1
export CAT_PLAYER_NAME="喵星人一号"
export CAT_MCP_INVOCATION_ID=<invocation-uuid>
export CAT_MCP_CALLBACK_TOKEN=<callback-token>

node cat-cafe-mcp-pipeline.js
```

### MCP工具说明

#### cat_cafe_post_message(content, message_type?)

主动向聊天室发送消息。

**参数：**
- `content` (string) - 消息内容
- `message_type` (enum) - 消息类型：
	- `normal` - 普通发言（默认）
	- `thinking` - 思考过程（会被拒绝，应保留在内心）
	- `action` - 行动声明

**示例：**
```javascript
await cat_cafe_post_message(
	"我觉得这个问题的关键在于平衡创新与安全",
	"normal"
);
```

#### cat_cafe_get_context(limit?)

获取最近的对话历史。

**参数：**
- `limit` (number) - 获取消息数量（默认10，最大50）

**返回：**
格式化的对话历史，包含发送者、内容和时间戳。

#### cat_cafe_broadcast_action(action_type, content, target?)

广播重要的行动声明（投票、夜晚行动等）。

**参数：**
- `action_type` (enum) - 行动类型：
	- `vote` - 投票
	- `night_action` - 夜晚行动
	- `ability_use` - 技能使用
	- `announcement` - 公告
- `content` (string) - 行动内容
- `target_player_id` (string, optional) - 目标玩家

### 最佳实践

#### ✅ 推荐做法

1. **先获取上下文再发言**
	 ```javascript
	 const context = await cat_cafe_get_context(10);
	 // 分析当前讨论进展
	 await cat_cafe_post_message("基于前面的讨论，我认为...");
	 ```

2. **区分内心独白和公开发言**
	 ```
	 思考："这个观点好像不太对..."（不发送）
	 组织语言... （不发送）
	 决定："我有些不同的想法想分享"（发送✅）
	 ```

3. **使用合适的消息类型**
	 ```javascript
	 // 日常讨论
	 await cat_cafe_post_message("今天天气不错", "normal");
   
	 // 重要决定
	 await cat_cafe_broadcast_action(
		 "vote",
		 "我投票给3 号玩家"
	 );
	 ```

#### ❌ 避免的做法

1. **不要发送思考过程**
	 ```javascript
	 // ❌ 错误
	 await cat_cafe_post_message("我在想要不要说这个...", "thinking");
   
	 // ✅ 正确
	 // 思考保留在内心，只发送结论
	 await cat_cafe_post_message("我有一个建议", "normal");
	 ```

2. **不要频繁刷屏**
	 - 等待其他参与者回应
	 - 每条消息应有实质内容
	 - 避免连续发送多条短消息

### 调试技巧

#### 查看 MCP 日志

```bash
# 启用详细日志
export DEBUG=mcp:*
node cat-cafe-mcp-pipeline.js

# 查看回调服务器日志
tail -f callback-server.log
```

#### 测试连接

```bash
# 测试回调服务器
curl http://localhost:3200/api/callbacks/health

# 测试 MCP Bridge
curl http://localhost:8000/api/mcp/health

# 手动发送测试消息
curl -X POST http://localhost:8000/api/mcp/post-message \\
	-H "Content-Type: application/json" \\
	-d '{
		"roomId": "test-room",
		"invocationId": "your-invocation-id",
		"callbackToken": "your-callback-token",
		"senderId": "test-user",
		"senderName": "Tester",
		"content": "Hello from curl!",
		"messageType": "normal"
	}'
```

### 故障排查

#### 问题：MCP Server 无法连接

**可能原因：**
- 环境变量未正确设置
- 后端服务未启动
- 端口被占用

**解决方法：**
```bash
# 检查环境变量
echo $CAT_MCP_BRIDGE_URL
echo $CAT_MCP_INVOCATION_ID

# 检查后端服务
curl http://localhost:8000/health

# 检查端口占用
netstat -ano | findstr :8000
```

#### 问题：认证失败(401)

**可能原因：**
- invocationId或 callbackToken不匹配
- 使用了过期或被撤销的凭证

**解决方法：**
1. 重启回调服务器生成新凭证
2. 确保 MCP Server使用正确的环境变量
3. 检查是否有拼写错误

#### 问题：消息未出现在聊天室

**可能原因：**
- 消息类型为`thinking`被拒绝
- 房间不存在或未激活
- 网络延迟或超时

**解决方法：**
1. 使用 `normal` 或`action` 类型
2. 确认房间已通过API 创建
3. 检查网络连接和防火墙设置

### 进阶：自定义MCP工具

你可以在[`cat-cafe-mcp-pipeline.js`](cat-cafe-mcp-pipeline.js) 中添加自定义工具：

```javascript
server.tool(
	'custom_tool_name',
	'工具描述',
	{
		param1: z.string().describe('参数 1描述'),
		param2: z.number().optional().default(10)
	},
	async ({ param1, param2 }) => {
		// 实现逻辑
		const result = await doSomething(param1, param2);
    
		return {
			content: [{ type: 'text', text: \`结果：${result}\` }]
		};
	}
);
```

### 相关文件

- [`callback-server.js`](callback-server.js) - HTTP回调服务器
- [`cat-cafe-mcp.js`](cat-cafe-mcp.js) - 基础MCP Server（单机模式）
- [`cat-cafe-mcp-pipeline.js`](cat-cafe-mcp-pipeline.js) - 流水线MCP Server
- [`run-cat.js`](run-cat.js) - 启动脚本（单机模式）
- [`backend/app/agent/mcp_callback_bridge.py`](backend/app/agent/mcp_callback_bridge.py) - Python MCP 桥接
- [`backend/app/agent/autonomous_chat_orchestrator.py`](backend/app/agent/autonomous_chat_orchestrator.py) - 自主聊天编排器
- [`scripts/start_autonomous_chat.ps1`](scripts/start_autonomous_chat.ps1) - 自主聊天启动脚本

## 📚 更多文档

- 流水线多端口多进程使用说明：`docs/pipeline_multi_cli_usage.md`
