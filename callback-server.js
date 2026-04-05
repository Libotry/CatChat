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

// ============ Intention State (F200 P0: 意图气泡) ============
const INTENTION_TTL_MS = 5 * 60 * 1000; // 5min TTL
const intentionMap = new Map(); // key: catId or "catId:taskId"
const intentionTimers = new Map();

function setIntention(entry) {
  const key = `${entry.catId}:${entry.taskId ?? '_global_'}`;
  clearIntentionTimer(key);
  intentionMap.set(key, { ...entry, reportedAt: Date.now() });
  intentionTimers.set(key, setTimeout(() => {
    intentionMap.delete(key);
    intentionTimers.delete(key);
  }, INTENTION_TTL_MS));
}

function clearIntentionTimer(key) {
  if (intentionTimers.has(key)) {
    clearTimeout(intentionTimers.get(key));
    intentionTimers.delete(key);
  }
}

function getAllIntentions() {
  return Array.from(intentionMap.values());
}

// ============ Decision Log (F200 P0: 决策日志) ============
const decisionLog = new Map(); // id -> DecisionLogEntry
const decisionsByCat = new Map(); // catId -> Set<id>
const decisionsByTask = new Map(); // taskId -> Set<id>

function computeHash(prevHash, entry) {
  const data = JSON.stringify({ prevHash, ...entry });
  return crypto.createHash('sha256').update(data).digest('hex');
}

function addDecision(entry) {
  const entries = Array.from(decisionLog.values());
  const prevHash = entries.length > 0 ? entries[entries.length - 1].hash : null;
  const hash = computeHash(prevHash, entry);
  const full = { ...entry, hash };

  decisionLog.set(entry.id, full);

  if (!decisionsByCat.has(entry.catId)) decisionsByCat.set(entry.catId, new Set());
  decisionsByCat.get(entry.catId).add(entry.id);

  if (entry.taskId) {
    if (!decisionsByTask.has(entry.taskId)) decisionsByTask.set(entry.taskId, new Set());
    decisionsByTask.get(entry.taskId).add(entry.id);
  }

  return full;
}

function acknowledgeDecision(decisionId, catId) {
  const entry = decisionLog.get(decisionId);
  if (entry && !entry.acknowledgedBy.includes(catId)) {
    entry.acknowledgedBy.push(catId);
  }
}

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

  // ============ F200 P0: 意图上报 (report_intention) ============
  if (req.method === 'POST' && pathname === '/api/callbacks/report-intention') {
    let body = '';
    req.on('data', chunk => { body += chunk.toString(); });
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        const { invocationId: reqInvId, callbackToken: reqToken, intent, type, taskId, blockers, urgency } = data;

        if (reqInvId !== invocationId || reqToken !== callbackToken) {
          res.statusCode = 401;
          res.end(JSON.stringify({ error: 'Unauthorized' }));
          return;
        }

        const entry = { intent, type, taskId, blockers, urgency: urgency ?? 0.5 };
        setIntention(entry);

        console.log(`[intention] ${type}: ${intent}${taskId ? ` (task=${taskId})` : ''}`);
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok', ...entry }));
      } catch {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }

  // ============ F200 P0: 清除意图 (clear_intention) ============
  if (req.method === 'POST' && pathname === '/api/callbacks/clear-intention') {
    let body = '';
    req.on('data', chunk => { body += chunk.toString(); });
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        const { invocationId: reqInvId, callbackToken: reqToken, taskId } = data;

        if (reqInvId !== invocationId || reqToken !== callbackToken) {
          res.statusCode = 401;
          res.end(JSON.stringify({ error: 'Unauthorized' }));
          return;
        }

        const prefix = `:${taskId ?? '_global_'}`;
        for (const key of intentionMap.keys()) {
          if (key.endsWith(prefix)) {
            clearIntentionTimer(key);
            intentionMap.delete(key);
          }
        }

        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok' }));
      } catch {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }

  // ============ F200 P0: 创建决策 (create_decision) ============
  if (req.method === 'POST' && pathname === '/api/callbacks/create-decision') {
    let body = '';
    req.on('data', chunk => { body += chunk.toString(); });
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        const { invocationId: reqInvId, callbackToken: reqToken, decision, rationale, evidence, relatedDecisions, taskId, supersededBy, catId } = data;

        if (reqInvId !== invocationId || reqToken !== callbackToken) {
          res.statusCode = 401;
          res.end(JSON.stringify({ error: 'Unauthorized' }));
          return;
        }

        const id = crypto.randomUUID();
        const entry = addDecision({
          id,
          decision,
          rationale,
          evidence: evidence ?? [],
          relatedDecisions: relatedDecisions ?? [],
          taskId,
          supersededBy,
          catId: catId ?? 'unknown',
          acknowledgedBy: [],
          timestamp: Date.now(),
        });

        console.log(`[decision-log][${catId}] created: ${decision.slice(0, 60)}...`);
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok', decision: entry }));
      } catch {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }

  // ============ F200 P0: 确认决策 (acknowledge_decision) ============
  if (req.method === 'POST' && pathname === '/api/callbacks/acknowledge-decision') {
    let body = '';
    req.on('data', chunk => { body += chunk.toString(); });
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        const { invocationId: reqInvId, callbackToken: reqToken, decisionId, catId } = data;

        if (reqInvId !== invocationId || reqToken !== callbackToken) {
          res.statusCode = 401;
          res.end(JSON.stringify({ error: 'Unauthorized' }));
          return;
        }

        if (!decisionLog.has(decisionId)) {
          res.statusCode = 404;
          res.end(JSON.stringify({ error: 'Decision not found' }));
          return;
        }

        acknowledgeDecision(decisionId, catId ?? 'unknown');
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok' }));
      } catch {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }

  // ============ F200 P0: 列出决策 (list_decisions) ============
  if (req.method === 'GET' && pathname === '/api/callbacks/list-decisions') {
    const { invocationId: reqInvId, callbackToken: reqToken, catId, taskId, limit } = parsedUrl.query;

    if (reqInvId !== invocationId || reqToken !== callbackToken) {
      res.statusCode = 401;
      res.end(JSON.stringify({ error: 'Unauthorized' }));
      return;
    }

    let ids;
    if (taskId) {
      ids = Array.from(decisionsByTask.get(taskId) ?? []);
    } else if (catId) {
      ids = Array.from(decisionsByCat.get(catId) ?? []);
    } else {
      ids = Array.from(decisionLog.keys());
    }

    const max = parseInt(limit) || 20;
    const decisions = ids
      .map(id => decisionLog.get(id))
      .filter(Boolean)
      .slice(-max)
      .reverse();

    res.statusCode = 200;
    res.end(JSON.stringify({ status: 'ok', decisions }));
    return;
  }

  // ============ F200 P0: 检查上下文 (check_context) ============
  if (req.method === 'GET' && pathname === '/api/callbacks/check-context') {
    const { invocationId: reqInvId, callbackToken: reqToken, targetAction, catId } = parsedUrl.query;

    if (reqInvId !== invocationId || reqToken !== callbackToken) {
      res.statusCode = 401;
      res.end(JSON.stringify({ error: 'Unauthorized' }));
      return;
    }

    const cat = catId ?? 'unknown';
    const relevant = Array.from(decisionLog.values())
      .filter(d => d.acknowledgedBy.includes(cat))
      .filter(d =>
        d.decision.toLowerCase().includes((targetAction ?? '').toLowerCase()) ||
        d.rationale.toLowerCase().includes((targetAction ?? '').toLowerCase())
      )
      .slice(-5);

    res.statusCode = 200;
    res.end(JSON.stringify({
      status: 'ok',
      hasContext: relevant.length > 0,
      relevantDecisions: relevant.map(d => d.id),
      suggestion: relevant.length > 0 ? null : '建议先阅读相关决策记录',
    }));
    return;
  }

  // ============ F200 P0: 获取所有意图 (intention list) ============
  if (req.method === 'GET' && pathname === '/api/callbacks/intentions') {
    const { invocationId: reqInvId, callbackToken: reqToken } = parsedUrl.query;

    if (reqInvId !== invocationId || reqToken !== callbackToken) {
      res.statusCode = 401;
      res.end(JSON.stringify({ error: 'Unauthorized' }));
      return;
    }

    res.statusCode = 200;
    res.end(JSON.stringify({ status: 'ok', intentions: getAllIntentions() }));
    return;
  }

  // ============ Chat History: 获取聊天历史 (get_chat_history) ============
  if (req.method === 'GET' && pathname === '/api/callbacks/chat-history') {
    const { invocationId: reqInvId, callbackToken: reqToken, roomId, limit } = parsedUrl.query;

    if (reqInvId !== invocationId || reqToken !== callbackToken) {
      res.statusCode = 401;
      res.end(JSON.stringify({ error: 'Unauthorized' }));
      return;
    }

    if (!roomId) {
      res.statusCode = 400;
      res.end(JSON.stringify({ error: 'roomId required' }));
      return;
    }

    const fs = require('fs');
    const path = require('path');
    const jsonlPath = path.join(__dirname, 'chat_history', `${roomId}.jsonl`);

    let messages = [];
    try {
      if (fs.existsSync(jsonlPath)) {
        const content = fs.readFileSync(jsonlPath, 'utf-8');
        const lines = content.split('\n').filter(l => l.trim());
        const n = Math.min(parseInt(limit) || 50, lines.length);
        const recent = lines.slice(-n);
        messages = recent.map(line => {
          try { return JSON.parse(line); } catch { return null; }
        }).filter(Boolean);
      }
    } catch (e) {
      res.statusCode = 500;
      res.end(JSON.stringify({ error: 'Read failed: ' + e.message }));
      return;
    }

    res.statusCode = 200;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ status: 'ok', roomId, messages, count: messages.length }));
    return;
  }

  // 404 for unknown routes
  res.statusCode = 404;
  res.end(JSON.stringify({ error: 'Not Found' }));
});

server.listen(3200, () => {
  console.log('Server ready to receive callbacks\n');
});
