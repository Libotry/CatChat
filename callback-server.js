const http = require('http');
const crypto = require('crypto');
const url = require('url');

// 启动时生成一对 UUID 作为凭证
const invocationId = crypto.randomUUID();
const callbackToken = crypto.randomUUID();

console.log('=== CatChat Callback Server ===');
console.log(`Invocation ID: ${invocationId}`);
console.log(`Callback Token: ${callbackToken}`);
console.log('Listening on port 3200...\n');

// 任务队列（先进先出）
const taskQueue = [];

// 模拟的对话历史
const mockThreadContext = {
  messages: [
    { role: 'user', content: '请写一首关于猫的诗' },
    { role: 'assistant', content: '好的，我来为你写一首关于猫的诗。' }
  ]
};

const server = http.createServer((req, res) => {
  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.pathname;

  // 设置 CORS 和 JSON 响应头
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Access-Control-Allow-Origin', '*');

  // POST /api/callbacks/post-message
  if (req.method === 'POST' && pathname === '/api/callbacks/post-message') {
    let body = '';
    
    req.on('data', chunk => {
      body += chunk.toString();
    });

    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        const { invocationId: reqInvId, callbackToken: reqToken, content } = data;

        // 验证凭证
        if (reqInvId !== invocationId || reqToken !== callbackToken) {
          res.statusCode = 401;
          res.end(JSON.stringify({ error: 'Unauthorized: invalid credentials' }));
          console.log('[401] Unauthorized callback attempt');
          return;
        }

        // 验证成功，打印消息到终端
        console.log('=== 📬 Message Posted to Chat Room ===');
        console.log(`Content: ${content}`);
        console.log('======================================\n');

        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok' }));
      } catch (err) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });

    return;
  }

  // GET /api/callbacks/thread-context
  if (req.method === 'GET' && pathname === '/api/callbacks/thread-context') {
    const { invocationId: reqInvId, callbackToken: reqToken } = parsedUrl.query;

    // 验证凭证
    if (reqInvId !== invocationId || reqToken !== callbackToken) {
      res.statusCode = 401;
      res.end(JSON.stringify({ error: 'Unauthorized: invalid credentials' }));
      console.log('[401] Unauthorized context request');
      return;
    }

    // 返回模拟的对话历史
    console.log('[200] Thread context requested');
    res.statusCode = 200;
    res.end(JSON.stringify(mockThreadContext));
    return;
  }

    // POST /api/tasks/dispatch - 前端下发任务
    if (req.method === 'POST' && pathname === '/api/tasks/dispatch') {
      let body = '';
    
      req.on('data', chunk => {
        body += chunk.toString();
      });

      req.on('end', () => {
        try {
          const data = JSON.parse(body);
          const { invocationId: reqInvId, callbackToken: reqToken, task } = data;

          // 验证凭证
          if (reqInvId !== invocationId || reqToken !== callbackToken) {
            res.statusCode = 401;
            res.end(JSON.stringify({ error: 'Unauthorized: invalid credentials' }));
            console.log('[401] Unauthorized task dispatch attempt');
            return;
          }

          if (!task || typeof task !== 'string') {
            res.statusCode = 400;
            res.end(JSON.stringify({ error: 'Invalid task: must be a non-empty string' }));
            return;
          }

          // 将任务加入队列
          const taskId = crypto.randomUUID();
          taskQueue.push({ id: taskId, task, timestamp: Date.now() });
        
          console.log(`✅ Task queued: ${taskId}`);
          console.log(`   Content: ${task.substring(0, 100)}${task.length > 100 ? '...' : ''}\n`);

          res.statusCode = 200;
          res.end(JSON.stringify({ status: 'queued', taskId }));
        } catch (err) {
          res.statusCode = 400;
          res.end(JSON.stringify({ error: 'Invalid JSON' }));
        }
      });

      return;
    }

    // GET /api/tasks/poll - 轮询获取下一个任务
    if (req.method === 'GET' && pathname === '/api/tasks/poll') {
      const { invocationId: reqInvId, callbackToken: reqToken } = parsedUrl.query;

      // 验证凭证
      if (reqInvId !== invocationId || reqToken !== callbackToken) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: 'Unauthorized: invalid credentials' }));
        console.log('[401] Unauthorized task poll attempt');
        return;
      }

      // 如果有任务，返回第一个；否则返回空
      if (taskQueue.length > 0) {
        const nextTask = taskQueue.shift();
        console.log(`📤 Task dispatched: ${nextTask.id}`);
        res.statusCode = 200;
        res.end(JSON.stringify(nextTask));
      } else {
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'waiting' }));
      }
      return;
    }

  // 404 for unknown routes
  res.statusCode = 404;
  res.end(JSON.stringify({ error: 'Not Found' }));
});

server.listen(3200, () => {
  console.log('Server ready to receive callbacks\n');
});
