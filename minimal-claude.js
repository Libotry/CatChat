const { spawn } = require('child_process');
const readline = require('readline');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const useEnvPrompt = args.includes('--from-env-b64');

// Parse --port for server mode (before building prompt so it's excluded)
let serverPort = parseInt(process.env.CATCHAT_PORT || '3456', 10);
const filteredArgs = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--from-env-b64') continue;
  if (args[i] === '--port' && args[i + 1]) {
    serverPort = parseInt(args[i + 1], 10) || 3456;
    i++; // skip the port value
    continue;
  }
  filteredArgs.push(args[i]);
}

let prompt = filteredArgs.join(' ').trim();
if (useEnvPrompt) {
  const b64 = process.env.CATCHAT_PROMPT_B64 || '';
  if (b64) {
    try {
      prompt = Buffer.from(b64, 'base64').toString('utf-8').trim();
    } catch (_) {}
  }
}
const isWindows = process.platform === 'win32';
const claudeBin = process.env.CLAUDE_BIN || 'claude';
const verboseEnabled = String(process.env.CATCHAT_CLI_VERBOSE || '').trim() === '1';
const PROXY_TIMEOUT_MS = 3600000;
const claudeArgs = ['-p', prompt];
if (verboseEnabled) {
  // Claude CLI requires --verbose when using --print (-p) with stream-json output.
  claudeArgs.push('--output-format', 'stream-json', '--verbose');
}

// Print the effective model env for observability in the parent proxy logs.
console.error('ANTHROPIC_MODEL=' + String(process.env.ANTHROPIC_MODEL || ''));

function resolveWorkingDirectory(raw) {
  const input = String(raw || '').trim();
  if (!input) return __dirname;
  const resolved = path.isAbsolute(input) ? input : path.resolve(__dirname, input);
  fs.mkdirSync(resolved, { recursive: true });
  const st = fs.statSync(resolved);
  if (!st.isDirectory()) {
    throw new Error('工作目录不是文件夹: ' + resolved);
  }
  return resolved;
}

if (!prompt) {
  // ====================== Server Mode ======================
  // When run without a prompt, start an HTTP server that accepts
  // requests from the CatChat frontend and proxies them to Claude CLI.
  const http = require('http');
  const https = require('https');

  function setCorsHeaders(res) {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', '*');
    res.setHeader('Access-Control-Max-Age', '86400');
  }

  function readBody(req) {
    return new Promise((resolve, reject) => {
      let body = '';
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => resolve(body));
      req.on('error', reject);
    });
  }

  function sanitizeSwitchCommand(raw) {
    const s = String(raw || '').trim();
    if (!s) return '';
    // Keep this intentionally strict: command name + optional dashes/underscores/dots.
    if (!/^[a-zA-Z0-9_.-]+$/.test(s)) {
      throw new Error('切换命令格式非法，仅允许字母数字与 ._-');
    }
    return s;
  }

  function spawnClaudeForPrompt(reqPrompt, timeoutMs, switchCommand, workingDir) {
    return new Promise((resolve, reject) => {
      const scriptPath = __filename;
      let effectiveWorkDir;
      try {
        effectiveWorkDir = resolveWorkingDirectory(workingDir);
      } catch (err) {
        reject(err);
        return;
      }
      let child;

      if (isWindows && String(switchCommand || '').trim()) {
        let cmd;
        try {
          cmd = sanitizeSwitchCommand(switchCommand);
        } catch (err) {
          reject(err);
          return;
        }

        const nodeExe = String(process.execPath || 'node').replace(/'/g, "''");
        const scriptEscaped = String(scriptPath || '').replace(/'/g, "''");
        const psScript = [
          "$ErrorActionPreference='Stop'",
          'if (Test-Path $PROFILE) { . $PROFILE }',
          cmd,
          "& '" + nodeExe + "' '" + scriptEscaped + "' --from-env-b64",
          'exit $LASTEXITCODE'
        ].join('; ');

        child = spawn('powershell.exe', ['-NoLogo', '-NoProfile', '-Command', psScript], {
          cwd: effectiveWorkDir,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: Object.assign({}, process.env, {
            CATCHAT_PROMPT_B64: Buffer.from(String(reqPrompt || ''), 'utf-8').toString('base64'),
            CATCHAT_WORKDIR: effectiveWorkDir
          }),
        });
      } else {
        const childArgs = [scriptPath, reqPrompt];
        child = spawn(process.execPath, childArgs, {
          cwd: effectiveWorkDir,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: Object.assign({}, process.env, {
            CATCHAT_WORKDIR: effectiveWorkDir
          }),
        });
      }

      let stdout = '';
      let stderr = '';
      let settled = false;

      const timer = setTimeout(() => {
        if (!settled) {
          settled = true;
          try { child.kill(); } catch (_) {}
          reject(new Error('Claude CLI 调用超时（' + timeoutMs + 'ms）'));
        }
      }, timeoutMs);

      child.stdout.on('data', (chunk) => { stdout += chunk; });
      child.stderr.on('data', (chunk) => { stderr += chunk; });
      child.on('error', (err) => {
        if (!settled) { settled = true; clearTimeout(timer); reject(err); }
      });
      child.on('close', (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        // Filter out status lines from stdout
        const lines = stdout.split(/\r?\n/).filter((l) => {
          const t = l.trim();
          if (!t) return false;
          if (t.startsWith('[No assistant text found in stream output]')) return false;
          if (t.startsWith('Claude process exited with code:')) return false;
          if (t.startsWith('Claude process ended by signal:')) return false;
          return true;
        });
        const replyText = lines.join('\n').trim();
        if (code === 0 && replyText) {
          resolve({ reply: replyText, stderr: stderr.trim() });
        } else {
          reject(new Error(stderr.trim() || 'Claude CLI 退出码: ' + code));
        }
      });
    });
  }

  function proxyRequest(targetUrl, method, headers, body) {
    return new Promise((resolve, reject) => {
      const parsed = new URL(targetUrl);
      const transport = parsed.protocol === 'https:' ? https : http;
      const proxyHeaders = Object.assign({}, headers);
      delete proxyHeaders['host'];
      delete proxyHeaders['origin'];
      delete proxyHeaders['referer'];
      delete proxyHeaders['connection'];
      delete proxyHeaders['accept-encoding'];
      const options = {
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
        path: parsed.pathname + parsed.search,
        method: method,
        headers: proxyHeaders,
        timeout: PROXY_TIMEOUT_MS,
      };
      const proxyReq = transport.request(options, (proxyRes) => {
        let chunks = [];
        proxyRes.on('data', (chunk) => { chunks.push(chunk); });
        proxyRes.on('end', () => {
          resolve({ statusCode: proxyRes.statusCode, headers: proxyRes.headers, body: Buffer.concat(chunks).toString('utf-8') });
        });
      });
      proxyReq.on('error', reject);
      proxyReq.on('timeout', () => { proxyReq.destroy(); reject(new Error('代理请求超时 (3600s)')); });
      if (body) proxyReq.write(body);
      proxyReq.end();
    });
  }

  let requestCount = 0;
  const server = http.createServer(async (req, res) => {
    setCorsHeaders(res);
    if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

    if (req.url === '/health' || req.url === '/ping') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok', name: 'minimal-claude server', uptime: process.uptime(), requests: requestCount }));
      return;
    }

    if (req.url === '/claude-code' && req.method === 'POST') {
      requestCount++;
      const reqId = requestCount;
      try {
        const raw = await readBody(req);
        const payload = JSON.parse(raw || '{}');
        const reqPrompt = String(payload.prompt || '').trim();
        const timeoutMsRaw = Number(payload.timeoutMs || 240000);
        const timeoutMs = Number.isFinite(timeoutMsRaw) ? Math.max(10000, Math.min(3600000, timeoutMsRaw)) : 240000;
        const switchCommand = String(payload.switchCommand || '').trim();
        const workingDir = String(payload.workingDir || '').trim();
        if (!reqPrompt) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: '缺少 prompt 参数' }));
          return;
        }
        console.error(`[#${reqId}] 📤 Claude CLI 请求 (${reqPrompt.length} chars)`);
        if (switchCommand) {
          console.error(`[#${reqId}] 🔀 模型切换命令: ${switchCommand}`);
        }
        if (workingDir) {
          console.error(`[#${reqId}] 📁 工作目录: ${workingDir}`);
        }
        const result = await spawnClaudeForPrompt(reqPrompt, timeoutMs, switchCommand, workingDir);
        console.error(`[#${reqId}] ✅ 完成`);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, reply: result.reply, stderr: result.stderr || '' }));
      } catch (err) {
        console.error(`[#${reqId}] ❌ ${err.message}`);
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: String(err && err.message || 'Claude CLI 执行失败') }));
      }
      return;
    }

    if (req.url === '/proxy' && req.method === 'POST') {
      requestCount++;
      try {
        const raw = await readBody(req);
        const payload = JSON.parse(raw);
        const targetUrl = payload.targetUrl;
        if (!targetUrl) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: '缺少 targetUrl 参数' }));
          return;
        }
        const targetBody = payload.body ? (typeof payload.body === 'string' ? payload.body : JSON.stringify(payload.body)) : '';
        const result = await proxyRequest(targetUrl, payload.method || 'POST', payload.headers || {}, targetBody);
        res.writeHead(result.statusCode, { 'Content-Type': result.headers['content-type'] || 'application/json' });
        res.end(result.body);
      } catch (err) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: '代理请求失败: ' + err.message }));
      }
      return;
    }

    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not Found. 可用端点: POST /claude-code, POST /proxy, GET /health' }));
  });

  server.listen(serverPort, () => {
    console.log('');
    console.log('  🐱 minimal-claude 服务器已启动');
    console.log('');
    console.log(`  ✓ 地址:       http://localhost:${serverPort}`);
    console.log(`  ✓ Claude端点:  POST /claude-code`);
    console.log(`  ✓ 代理端点:   POST /proxy`);
    console.log(`  ✓ 健康检查:   GET  /health`);
    console.log('');
    console.log('  📋 在 CatChat 前端「本地 CLI 代理」中');
    console.log(`     填入 http://localhost:${serverPort} 并开启开关即可`);
    console.log('');
  });

  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      console.error(`❌ 端口 ${serverPort} 已被占用！使用 --port <端口> 指定其他端口。`);
    } else {
      console.error('❌ 服务器错误:', err.message);
    }
    process.exit(1);
  });

  process.on('SIGINT', () => {
    console.log('\n👋 minimal-claude 服务器已停止。');
    process.exit(0);
  });

  // Prevent falling through to single-shot mode
  return;
}

function resolveWindowsClaudeCliEntrypoint() {
  const appData = process.env.APPDATA || '';
  const candidate = path.join(
    appData,
    'npm',
    'node_modules',
    '@anthropic-ai',
    'claude-code',
    'cli.js'
  );
  return fs.existsSync(candidate) ? candidate : '';
}

const child = (() => {
  const effectiveWorkDir = resolveWorkingDirectory(process.env.CATCHAT_WORKDIR || '');
  if (!isWindows) {
    return spawn(claudeBin, claudeArgs, {
      cwd: effectiveWorkDir,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  }

  // Prefer launching the Claude CLI JS entrypoint directly via node to avoid
  // cmd.exe argument parsing issues with multi-line prompts.
  if (!process.env.CLAUDE_BIN) {
    const cliJs = resolveWindowsClaudeCliEntrypoint();
    if (cliJs) {
      return spawn(process.execPath, [cliJs, ...claudeArgs], {
        cwd: effectiveWorkDir,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    }
  }

  return spawn(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', claudeBin, ...claudeArgs], {
    cwd: effectiveWorkDir,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
})();

const rl = readline.createInterface({
  input: child.stdout,
  crlfDelay: Infinity,
});

let hasAssistantText = false;
let plainTextMode = false;
let lastUsageLine = '';
let lastCumulativeText = '';

function pickUsage(obj) {
  if (!obj || typeof obj !== 'object') return null;
  if (obj.usage && typeof obj.usage === 'object') return obj.usage;

  const directKeys = [
    'input_tokens',
    'output_tokens',
    'total_tokens',
    'cache_creation_input_tokens',
    'cache_read_input_tokens',
    'reasoning_tokens',
  ];
  const hasDirect = directKeys.some((k) => typeof obj[k] === 'number');
  if (hasDirect) return obj;

  for (const k of Object.keys(obj)) {
    const v = obj[k];
    if (v && typeof v === 'object') {
      const hit = pickUsage(v);
      if (hit) return hit;
    }
  }
  return null;
}

function emitUsageFromEvent(event) {
  const usage = pickUsage(event);
  if (!usage) return;

  const fields = [
    ['input_tokens', usage.input_tokens],
    ['output_tokens', usage.output_tokens],
    ['total_tokens', usage.total_tokens],
    ['cache_creation_input_tokens', usage.cache_creation_input_tokens],
    ['cache_read_input_tokens', usage.cache_read_input_tokens],
    ['reasoning_tokens', usage.reasoning_tokens],
  ].filter((x) => typeof x[1] === 'number');

  if (!fields.length) return;
  const line = 'TOKEN_USAGE ' + fields.map((x) => x[0] + '=' + x[1]).join(' ');
  if (line === lastUsageLine) return;
  lastUsageLine = line;
  console.error(line);
}

function collectTextBlocks(value, cumulative, out) {
  if (!value) return;
  if (typeof value === 'string') {
    out.push({ text: value, cumulative: !!cumulative });
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      collectTextBlocks(item, cumulative, out);
    }
    return;
  }
  if (typeof value !== 'object') return;

  if (typeof value.text === 'string') {
    out.push({ text: value.text, cumulative: !!cumulative });
  }
  if (typeof value.result === 'string') {
    out.push({ text: value.result, cumulative: true });
  }
  if (typeof value.output_text === 'string') {
    out.push({ text: value.output_text, cumulative: true });
  }
  if (value.delta && typeof value.delta.text === 'string') {
    out.push({ text: value.delta.text, cumulative: false });
  }

  if (value.message) {
    collectTextBlocks(value.message.content, true, out);
    if (value.message.delta && typeof value.message.delta.text === 'string') {
      out.push({ text: value.message.delta.text, cumulative: false });
    }
  }

  if (value.content) {
    collectTextBlocks(value.content, true, out);
  }
}

function emitTextFromEvent(event) {
  const blocks = [];
  collectTextBlocks(event, false, blocks);
  for (const block of blocks) {
    const text = String(block && block.text || '');
    if (!text) continue;
    hasAssistantText = true;
    if (block.cumulative) {
      if (text.startsWith(lastCumulativeText)) {
        const suffix = text.slice(lastCumulativeText.length);
        if (suffix) process.stdout.write(suffix);
      } else if (lastCumulativeText && lastCumulativeText.startsWith(text)) {
        // Ignore regressive partial snapshots.
      } else {
        process.stdout.write(text);
      }
      lastCumulativeText = text;
    } else {
      process.stdout.write(text);
      lastCumulativeText += text;
    }
  }
}

rl.on('line', (line) => {
  const trimmed = line.trim();
  if (!trimmed) {
    return;
  }

  let event;
  try {
    event = JSON.parse(trimmed);
  } catch (err) {
    // Some Claude CLI versions return plain text instead of stream-json lines.
    // Fall back gracefully instead of treating this as an error.
    if (!plainTextMode && hasAssistantText) {
      process.stdout.write('\n');
    }
    plainTextMode = true;
    hasAssistantText = true;
    process.stdout.write(trimmed + '\n');
    return;
  }

  emitUsageFromEvent(event);
  emitTextFromEvent(event);
});

child.stderr.on('data', (chunk) => {
  process.stderr.write(chunk);
});

child.on('error', (err) => {
  console.error('Failed to start Claude CLI:', err.message);
  if (err && err.code === 'ENOENT') {
    console.error(
      'Tip: ensure Claude CLI is in PATH, or set CLAUDE_BIN (e.g. CLAUDE_BIN=claude.cmd on Windows).'
    );
  }
});

child.on('close', (code, signal) => {
  rl.close();

  if (!hasAssistantText) {
    console.log('\n[No assistant text found in stream output]');
  } else {
    console.log('');
  }

  if (signal) {
    console.log(`Claude process ended by signal: ${signal}`);
    process.exitCode = 1;
  } else {
    console.log(`Claude process exited with code: ${code}`);
    process.exitCode = (typeof code === 'number') ? code : 1;
  }
});
