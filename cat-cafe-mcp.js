#!/usr/bin/env node

const { McpServer } = require('@modelcontextprotocol/sdk/server/mcp.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { z } = require('zod');

// 从环境变量读取配置
const API_URL = process.env.CAT_CAFE_API_URL || 'http://localhost:3200';
const INVOCATION_ID = process.env.CAT_CAFE_INVOCATION_ID;
const CALLBACK_TOKEN = process.env.CAT_CAFE_CALLBACK_TOKEN;

if (!INVOCATION_ID || !CALLBACK_TOKEN) {
  console.error('Error: Missing required environment variables');
  console.error('Please set: CAT_CAFE_INVOCATION_ID, CAT_CAFE_CALLBACK_TOKEN');
  console.error('Optionally set: CAT_CAFE_API_URL (default: http://localhost:3200)');
  process.exit(1);
}

// 创建 MCP Server
const server = new McpServer({
  name: 'cat-cafe',
  version: '1.0.0'
});

// 工具 1: cat_cafe_post_message - 发送消息到聊天室
server.tool(
  'cat_cafe_post_message',
  '主动向聊天室发送消息（AI 的"发言"）',
  {
    content: z.string().describe('要发送到聊天室的消息内容')
  },
  async ({ content }) => {
    try {
      const response = await fetch(`${API_URL}/api/callbacks/post-message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          invocationId: INVOCATION_ID,
          callbackToken: CALLBACK_TOKEN,
          content: content
        })
      });

      if (response.status === 401) {
        return {
          content: [
            {
              type: 'text',
              text: '❌ Authentication failed: invalid invocationId or callbackToken'
            }
          ],
          isError: true
        };
      }

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      
      return {
        content: [
          {
            type: 'text',
            text: `✅ Message posted successfully: ${JSON.stringify(result)}`
          }
        ]
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `❌ Failed to post message: ${error.message}`
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
  '获取当前对话上下文的模拟数据',
  {},
  async () => {
    try {
      const url = new URL(`${API_URL}/api/callbacks/thread-context`);
      url.searchParams.set('invocationId', INVOCATION_ID);
      url.searchParams.set('callbackToken', CALLBACK_TOKEN);

      const response = await fetch(url.toString(), {
        method: 'GET'
      });

      if (response.status === 401) {
        return {
          content: [
            {
              type: 'text',
              text: '❌ Authentication failed: invalid invocationId or callbackToken'
            }
          ],
          isError: true
        };
      }

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const context = await response.json();
      
      return {
        content: [
          {
            type: 'text',
            text: `📖 Thread Context:\n${JSON.stringify(context, null, 2)}`
          }
        ]
      };
    } catch (error) {
      return {
        content: [
          {
            type: 'text',
            text: `❌ Failed to get context: ${error.message}`
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
  console.error('Cat Cafe MCP Server running on stdio');
  console.error(`API URL: ${API_URL}`);
}

main().catch(console.error);
