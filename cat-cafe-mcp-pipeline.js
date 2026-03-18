#!/usr/bin/env node

/**
 * Cat Cafe MCP Server - Pipeline Mode
 * 
 * 这个 MCP Server 专为流水线模式设计，支持：
 * - 从环境变量读取房间和猫猫配置
 * - 自动连接到 MCP Bridge
 * - 支持多个猫猫并发调用
 */

const { McpServer } = require('@modelcontextprotocol/sdk/server/mcp.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { z } = require('zod');

// 从环境变量读取配置（由父进程注入）
const BRIDGE_URL = process.env.CAT_MCP_BRIDGE_URL || 'http://localhost:8000';
const ROOM_ID = process.env.CAT_ROOM_ID;
const PLAYER_ID = process.env.CAT_PLAYER_ID;
const PLAYER_NAME = process.env.CAT_PLAYER_NAME || 'Unknown';
const INVOCATION_ID = process.env.CAT_MCP_INVOCATION_ID;
const CALLBACK_TOKEN = process.env.CAT_MCP_CALLBACK_TOKEN;

// 验证必需的环境变量
if (!ROOM_ID || !PLAYER_ID || !INVOCATION_ID || !CALLBACK_TOKEN) {
  console.error('Error: Missing required environment variables');
  console.error('Required:');
  console.error('  CAT_ROOM_ID - 房间 ID');
  console.error('  CAT_PLAYER_ID - 玩家 ID');
  console.error('  CAT_MCP_INVOCATION_ID - MCP 调用 ID');
  console.error('  CAT_MCP_CALLBACK_TOKEN - MCP 回调令牌');
  console.error('Optional:');
  console.error('  CAT_PLAYER_NAME - 玩家名称（默认：Unknown）');
  console.error('  CAT_MCP_BRIDGE_URL - MCP Bridge URL（默认：http://localhost:8000）');
  process.exit(1);
}

// 创建 MCP Server
const server = new McpServer({
  name: 'cat-cafe-pipeline',
  version: '1.0.0',
  description: `MCP Server for ${PLAYER_NAME} in room ${ROOM_ID}`
});

// 工具 1: cat_cafe_post_message - 发送消息到聊天室
server.tool(
  'cat_cafe_post_message',
  '主动向聊天室发送消息（AI 的"发言"）。这是你与其他猫猫交流的主要方式。',
  {
    content: z.string().describe('要发送到聊天室的消息内容'),
    message_type: z.enum(['normal', 'thinking', 'action']).optional().default('normal')
      .describe('消息类型：normal=普通发言，thinking=思考过程（通常不公开），action=行动声明')
  },
  async ({ content, message_type = 'normal' }) => {
    const startTime = Date.now();
    
    try {
      // 只允许发送 normal 和 action 类型的消息
      // thinking 类型应该保留在内心独白中
      if (message_type === 'thinking') {
        return {
          content: [
            {
              type: 'text',
              text: '⚠️ 思考过程不应该发送给其他猫猫看。请将重要结论或决定用 normal 类型发送。'
            }
          ],
          isError: false,
          metadata: { suggestion: 'Consider using message_type="normal" for shareable thoughts' }
        };
      }

      const response = await fetch(`${BRIDGE_URL}/api/mcp/post-message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          roomId: ROOM_ID,
          invocationId: INVOCATION_ID,
          callbackToken: CALLBACK_TOKEN,
          senderId: PLAYER_ID,
          senderName: PLAYER_NAME,
          content: content,
          messageType: message_type
        })
      });

      const latency = Date.now() - startTime;

      if (response.status === 401) {
        return {
          content: [
            {
              type: 'text',
              text: '❌ 认证失败：invocationId 或 callbackToken 无效'
            }
          ],
          isError: true
        };
      }

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errorText}`);
      }

      const result = await response.json();
      
      return {
        content: [
          {
            type: 'text',
            text: `✅ 消息已发送 (${latency}ms)\n消息 ID: ${result.message_id}\n内容预览：${content.substring(0, 50)}${content.length > 50 ? '...' : ''}`
          }
        ],
        metadata: {
          messageId: result.message_id,
          latency: latency,
          messageType: message_type
        }
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `❌ 发送失败：${error.message}`
          }
        ],
        isError: true
      };
    }
  }
);

// 工具 2: cat_cafe_get_context - 获取对话上下文
server.tool(
  'cat_cafe_get_context',
  '获取当前聊天室的最近对话历史。在发言前使用此工具了解当前的讨论进展。',
  {
    limit: z.number().optional().default(10)
      .describe('获取最近多少条消息（默认：10）')
  },
  async ({ limit = 10 }) => {
    const startTime = Date.now();
    
    try {
      const url = new URL(`${BRIDGE_URL}/api/mcp/get-context`);
      url.searchParams.set('roomId', ROOM_ID);
      url.searchParams.set('invocationId', INVOCATION_ID);
      url.searchParams.set('callbackToken', CALLBACK_TOKEN);
      url.searchParams.set('limit', String(limit));

      const response = await fetch(url.toString(), {
        method: 'GET'
      });

      const latency = Date.now() - startTime;

      if (response.status === 401) {
        return {
          content: [
            {
              type: 'text',
              text: '❌ 认证失败：invocationId 或 callbackToken 无效'
            }
          ],
          isError: true
        };
      }

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errorText}`);
      }

      const context = await response.json();
      const messageCount = context.messages?.length || 0;
      
      // 格式化输出
      let formattedText = `📖 对话上下文（最近${messageCount}条消息，耗时${latency}ms）\n\n`;
      
      if (messageCount === 0) {
        formattedText += '(暂无历史消息，你可以率先发言)\n';
      } else {
        for (const msg of context.messages) {
          const emoji = msg.message_type === 'action' ? '🎯' : '💬';
          formattedText += `${emoji} [${msg.sender_name}] ${msg.content}\n`;
          if (msg.content.length > 100) {
            formattedText += `   (消息长度：${msg.content.length}字)\n`;
          }
        }
      }
      
      return {
        content: [
          {
            type: 'text',
            text: formattedText.trim()
          }
        ],
        metadata: {
          messageCount: messageCount,
          latency: latency,
          roomId: context.room_id
        }
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `❌ 获取上下文失败：${error.message}`
          }
        ],
        isError: true
      };
    }
  }
);

// 工具 3: cat_cafe_broadcast_action - 广播行动声明（特殊类型）
server.tool(
  'cat_cafe_broadcast_action',
  '广播重要的行动声明（例如：投票决定、夜晚行动等）。这会触发特殊的事件处理。',
  {
    action_type: z.enum(['vote', 'night_action', 'ability_use', 'announcement'])
      .describe('行动类型'),
    content: z.string().describe('行动的具体内容'),
    target_player_id: z.string().optional().describe('目标玩家 ID（如果有）')
  },
  async ({ action_type, content, target_player_id }) => {
    try {
      const response = await fetch(`${BRIDGE_URL}/api/mcp/post-message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          roomId: ROOM_ID,
          invocationId: INVOCATION_ID,
          callbackToken: CALLBACK_TOKEN,
          senderId: PLAYER_ID,
          senderName: PLAYER_NAME,
          content: content,
          messageType: 'action',
          metadata: {
            actionType: action_type,
            targetPlayerId: target_player_id
          }
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      
      return {
        content: [
          {
            type: 'text',
            text: `🎯 行动已广播\n类型：${action_type}\n消息 ID: ${result.message_id}`
          }
        ]
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `❌ 广播失败：${error.message}`
          }
        ],
        isError: true
      };
    }
  }
);

// 启动服务器
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  
  // 输出启动信息到 stderr（不干扰 stdio 通信）
  console.error('🐱 Cat Cafe MCP Server (Pipeline Mode)');
  console.error(`   Player: ${PLAYER_NAME} (${PLAYER_ID})`);
  console.error(`   Room: ${ROOM_ID}`);
  console.error(`   Bridge: ${BRIDGE_URL}`);
  console.error('   Status: Ready\n');
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
