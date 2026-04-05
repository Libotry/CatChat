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

// ============ F200 P0: 意图上报 (report_intention) ============
// 类型: analyzing=蓝色(思考中), implementing=绿色(执行中), waiting=紫色(等待结果), blocked=琥珀色(阻塞)
server.tool(
  'cat_cafe_report_intention',
  '上报当前意图到聊天室侧边栏。触发时机：开始新任务或被阻塞时（非持续广播）',
  {
    intent: z.string().describe('当前正在做什么（如"分析需求文档"）'),
    type: z.enum(['analyzing', 'implementing', 'waiting', 'blocked']).describe('意图类型'),
    taskId: z.string().optional().describe('关联的任务 ID'),
    blockers: z.string().optional().describe('阻塞原因（仅 type=blocked 时）'),
    urgency: z.number().optional().describe('紧急度 0-1（默认 0.5）'),
  },
  async ({ intent, type, taskId, blockers, urgency }) => {
    try {
      const response = await fetch(`${API_URL}/api/callbacks/report-intention`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          invocationId: INVOCATION_ID,
          callbackToken: CALLBACK_TOKEN,
          intent,
          type,
          taskId,
          blockers,
          urgency: urgency ?? 0.5,
        })
      });

      if (response.status === 401) {
        return { content: [{ type: 'text', text: '❌ Authentication failed' }], isError: true };
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      return { content: [{ type: 'text', text: `✅ Intention reported: ${type} — ${intent}` }] };
    } catch (error) {
      return { content: [{ type: 'text', text: `❌ Failed: ${error.message}` }], isError: true };
    }
  }
);

// ============ F200 P0: 清除意图 (clear_intention) ============
server.tool(
  'cat_cafe_clear_intention',
  '清除已上报的意图（任务完成或阻塞解除时）',
  {
    taskId: z.string().optional().describe('任务 ID（省略则清除该猫所有意图）'),
  },
  async ({ taskId }) => {
    try {
      const response = await fetch(`${API_URL}/api/callbacks/clear-intention`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          invocationId: INVOCATION_ID,
          callbackToken: CALLBACK_TOKEN,
          taskId,
        })
      });

      if (response.status === 401) {
        return { content: [{ type: 'text', text: '❌ Authentication failed' }], isError: true };
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return { content: [{ type: 'text', text: '✅ Intention cleared' }] };
    } catch (error) {
      return { content: [{ type: 'text', text: `❌ Failed: ${error.message}` }], isError: true };
    }
  }
);

// ============ F200 P0: 创建决策 (create_decision) ============
server.tool(
  'cat_cafe_create_decision',
  '创建一条决策记录（哈希链不可篡改）',
  {
    decision: z.string().describe('决策内容摘要'),
    rationale: z.string().describe('为什么这样决定'),
    evidence: z.array(z.string()).optional().describe('参考文档/代码 URL'),
    relatedDecisions: z.array(z.string()).optional().describe('关联的决策 ID'),
    taskId: z.string().optional().describe('关联任务'),
    supersededBy: z.string().optional().describe('被哪个决策更新'),
  },
  async ({ decision, rationale, evidence, relatedDecisions, taskId, supersededBy }) => {
    try {
      const response = await fetch(`${API_URL}/api/callbacks/create-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          invocationId: INVOCATION_ID,
          callbackToken: CALLBACK_TOKEN,
          decision,
          rationale,
          evidence: evidence ?? [],
          relatedDecisions: relatedDecisions ?? [],
          taskId,
          supersededBy,
        })
      });

      if (response.status === 401) {
        return { content: [{ type: 'text', text: '❌ Authentication failed' }], isError: true };
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      return { content: [{ type: 'text', text: `✅ Decision created: ${decision.slice(0, 80)}...` }] };
    } catch (error) {
      return { content: [{ type: 'text', text: `❌ Failed: ${error.message}` }], isError: true };
    }
  }
);

// ============ F200 P0: 确认决策 (acknowledge_decision) ============
server.tool(
  'cat_cafe_acknowledge_decision',
  '确认一条决策（表示已阅读知晓）',
  {
    decisionId: z.string().describe('要确认的决策 ID'),
  },
  async ({ decisionId }) => {
    try {
      const response = await fetch(`${API_URL}/api/callbacks/acknowledge-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          invocationId: INVOCATION_ID,
          callbackToken: CALLBACK_TOKEN,
          decisionId,
        })
      });

      if (response.status === 401) {
        return { content: [{ type: 'text', text: '❌ Authentication failed' }], isError: true };
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return { content: [{ type: 'text', text: `✅ Decision acknowledged` }] };
    } catch (error) {
      return { content: [{ type: 'text', text: `❌ Failed: ${error.message}` }], isError: true };
    }
  }
);

// ============ F200 P0: 列出决策 (list_decisions) ============
server.tool(
  'cat_cafe_list_decisions',
  '查询决策日志（可按猫猫/任务过滤）',
  {
    catId: z.string().optional().describe('按猫猫过滤'),
    taskId: z.string().optional().describe('按任务过滤'),
    limit: z.number().optional().describe('返回数量（默认 20）'),
  },
  async ({ catId, taskId, limit }) => {
    try {
      const params = new URLSearchParams({ invocationId: INVOCATION_ID, callbackToken: CALLBACK_TOKEN });
      if (catId) params.set('catId', catId);
      if (taskId) params.set('taskId', taskId);
      if (limit) params.set('limit', String(limit));

      const response = await fetch(`${API_URL}/api/callbacks/list-decisions?${params}`, {
        method: 'GET',
      });

      if (response.status === 401) {
        return { content: [{ type: 'text', text: '❌ Authentication failed' }], isError: true };
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      const lines = result.decisions.map(d => `• [${d.id.slice(0, 8)}] ${d.decision.slice(0, 60)}...`).join('\n');
      return { content: [{ type: 'text', text: `📋 Decisions (${result.decisions.length}):\n${lines || '(none)'}` }] };
    } catch (error) {
      return { content: [{ type: 'text', text: `❌ Failed: ${error.message}` }], isError: true };
    }
  }
);

// ============ F200 P0: 检查上下文 (check_context) ============
server.tool(
  'cat_cafe_check_context',
  '第四授权维度：检查猫猫是否有足够上下文做某行动',
  {
    targetAction: z.string().describe('目标行动描述（用于匹配已确认的决策）'),
    catId: z.string().optional().describe('要检查的猫猫'),
  },
  async ({ targetAction, catId }) => {
    try {
      const params = new URLSearchParams({ invocationId: INVOCATION_ID, callbackToken: CALLBACK_TOKEN, targetAction });
      if (catId) params.set('catId', catId);

      const response = await fetch(`${API_URL}/api/callbacks/check-context?${params}`, {
        method: 'GET',
      });

      if (response.status === 401) {
        return { content: [{ type: 'text', text: '❌ Authentication failed' }], isError: true };
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      const ctx = result.hasContext ? '✅' : '⚠️';
      return {
        content: [{
          type: 'text',
          text: `${ctx} Context check for "${targetAction}": ${result.hasContext ? '有足够上下文' : '建议先阅读相关决策'}\nRelevant: ${result.relevantDecisions.length}`
        }]
      };
    } catch (error) {
      return { content: [{ type: 'text', text: `❌ Failed: ${error.message}` }], isError: true };
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
