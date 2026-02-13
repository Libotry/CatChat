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
