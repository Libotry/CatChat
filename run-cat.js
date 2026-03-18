#!/usr/bin/env node

const { spawn } = require('child_process');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// 检查依赖文件是否存在
const requiredFiles = ['callback-server.js', 'cat-cafe-mcp.js'];
for (const file of requiredFiles) {
  if (!fs.existsSync(path.join(__dirname, file))) {
    console.error(`Error: ${file} not found in current directory`);
    process.exit(1);
  }
}

// 检测 claude命令是否可用
try {
  execSync('claude --version', { stdio: 'ignore' });
} catch (err) {
  console.error('❌ 错误：未找到claude命令');
  console.error('');
  console.error('请先安装Claude CLI:');
  console.error('  npm install -g @anthropic-ai/claude-code');
  console.error('');
  console.error('然后登录:');
  console.error('  claude login');
  console.error('');
  process.exit(1);
}

// 从 callback-server.js 的输出中捕获凭证（需要在另一个终端运行）
console.log('=== CatChat MCP Runner ===\n');
console.log('⚠️  请确保已在另一个终端运行 callback-server.js 并记录了凭证\n');
console.log('示例启动命令:');
console.log('  node callback-server.js\n');

// 提示用户输入凭证
const readline = require('readline').createInterface({
  input: process.stdin,
  output: process.stdout
});

function askQuestion(question) {
  return new Promise(resolve => {
    readline.question(question, answer => {
      resolve(answer.trim());
    });
  });
}

// 从 callback-server 轮询任务
async function pollTask(invocationId, callbackToken) {
  const http = require('http');
  
  return new Promise((resolve, reject) => {
    const options = {
      hostname: 'localhost',
      port: 3200,
      path: `/api/tasks/poll?invocationId=${encodeURIComponent(invocationId)}&callbackToken=${encodeURIComponent(callbackToken)}`,
      method: 'GET'
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const response = JSON.parse(data);
          resolve(response);
        } catch (err) {
          reject(new Error('Failed to parse task response'));
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(5000, () => reject(new Error('Poll timeout')));
    req.end();
  });
}

// 等待并获取任务
async function waitForTask(invocationId, callbackToken) {
  console.log('⏳ 等待任务下发...（前端将通过 /api/tasks/dispatch 发送任务）\n');
  
  while (true) {
    try {
      const response = await pollTask(invocationId, callbackToken);
      
      if (response.status === 'waiting') {
        // 没有可用任务，等待 2 秒后重试
        await new Promise(r => setTimeout(r, 2000));
        process.stdout.write('.');
        continue;
      }
      
      // 有任务了
      console.log(`\n✅ 收到任务：${response.id}`);
      return response.task;
    } catch (err) {
      console.error('轮询任务失败:', err.message);
      await new Promise(r => setTimeout(r, 2000));
    }
  }
}

async function run() {
  try {
    const invocationId = await askQuestion('请输入 Invocation ID: ');
    const callbackToken = await askQuestion('请输入 Callback Token: ');
    
    if (!invocationId || !callbackToken) {
      console.error('Error: Both invocationId and callbackToken are required');
      readline.close();
      process.exit(1);
    }

    readline.close();

    // 等待前端下发任务
    const task = await waitForTask(invocationId, callbackToken);

    // 构建 MCP 配置
    const mcpConfig = {
      mcpServers: {
        'cat-cafe': {
          command: 'node',
          args: ['cat-cafe-mcp.js'],
          env: {
            CAT_CAFE_API_URL: 'http://localhost:3200',
            CAT_CAFE_INVOCATION_ID: invocationId,
            CAT_CAFE_CALLBACK_TOKEN: callbackToken
          }
        }
      }
    };

    console.log('\n🚀 Starting Claude CLI with Cat Cafe MCP Server...\n');
    console.log('💡 提示：如果是第一次使用，需要先登录Claude:');
    console.log('   claude login');
    console.log('');
    console.log('Task:', task);
    console.log('\n---\n');

    // 构建 claude 命令参数
    const cliArgs = [
      '-p', task,
      '--output-format', 'stream-json',
      '--verbose',
      '--mcp-config', JSON.stringify(mcpConfig)
    ];

    // spawn claude 进程
    // 修复安全警告：移除shell 选项，使用数组形式传递参数
    const child = spawn('claude', cliArgs, {
      stdio: 'inherit',
      env: { ...process.env }
    });

    child.on('close', (code) => {
      if (code !== 0) {
        console.error(`\n\n❌ Claude CLI退出，退出码：${code}`);
        if (code === 1) {
          console.error('\n可能的原因:');
          console.error('  1. 未登录：运行 claude login');
          console.error('  2. 配额不足：检查账户订阅');
          console.error('  3. 网络问题：检查网络连接');
        }
      } else {
        console.log('\n✅ 任务完成!');
      }
      process.exit(code);
    });

  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
}

run();
