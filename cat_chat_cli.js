#!/usr/bin/env node
// ============================================================
//  🐱 CatChat CLI — 本地代理服务器
//  解决浏览器 CORS 跨域限制，代理转发 API 请求
//  启动方式：node cat_chat_cli.js [--port 3456]
// ============================================================

const http = require('http');
const https = require('https');
const url = require('url');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

// ====================== Config ======================
const DEFAULT_PORT = 3456;
let PORT = DEFAULT_PORT;

// Parse CLI arguments
const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if ((args[i] === '--port' || args[i] === '-p') && args[i + 1]) {
        PORT = parseInt(args[i + 1], 10) || DEFAULT_PORT;
        i++;
    }
    if (args[i] === '--help' || args[i] === '-h') {
        console.log(`
  🐱 CatChat CLI — 本地代理服务器

  用法: node cat_chat_cli.js [选项]

  选项:
    -p, --port <端口>    指定服务端口 (默认: ${DEFAULT_PORT})
    -h, --help           显示帮助信息

  说明:
    启动后，在 CatChat 网页的「本地 CLI 代理」设置中
    填入 http://localhost:<端口> 并开启开关即可。
    所有 API 请求将通过本地代理转发，绕过浏览器 CORS 限制。
`);
        process.exit(0);
    }
}

// ====================== Color Helpers ======================
const C = {
    reset:   '\x1b[0m',
    bold:    '\x1b[1m',
    dim:     '\x1b[2m',
    red:     '\x1b[31m',
    green:   '\x1b[32m',
    yellow:  '\x1b[33m',
    blue:    '\x1b[34m',
    magenta: '\x1b[35m',
    cyan:    '\x1b[36m',
    white:   '\x1b[37m',
    bgBlue:  '\x1b[44m',
    bgGreen: '\x1b[42m',
    bgRed:   '\x1b[41m',
};

function log(icon, color, label, msg) {
    const ts = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    console.log(`${C.dim}[${ts}]${C.reset} ${icon} ${color}${C.bold}${label}${C.reset} ${msg || ''}`);
}

// ====================== CORS Headers ======================
function setCorsHeaders(res) {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', '*');
    res.setHeader('Access-Control-Max-Age', '86400');
}

// ====================== Proxy Logic ======================
function proxyRequest(targetUrl, method, headers, body) {
    return new Promise(function(resolve, reject) {
        const parsed = new URL(targetUrl);
        const transport = parsed.protocol === 'https:' ? https : http;

        // Forward headers, remove host-related ones
        const proxyHeaders = Object.assign({}, headers);
        delete proxyHeaders['host'];
        delete proxyHeaders['origin'];
        delete proxyHeaders['referer'];
        delete proxyHeaders['connection'];
        delete proxyHeaders['accept-encoding']; // avoid compressed responses

        const options = {
            hostname: parsed.hostname,
            port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
            path: parsed.pathname + parsed.search,
            method: method,
            headers: proxyHeaders,
            timeout: 120000
        };

        const proxyReq = transport.request(options, function(proxyRes) {
            let chunks = [];
            proxyRes.on('data', function(chunk) { chunks.push(chunk); });
            proxyRes.on('end', function() {
                const responseBody = Buffer.concat(chunks).toString('utf-8');
                resolve({
                    statusCode: proxyRes.statusCode,
                    headers: proxyRes.headers,
                    body: responseBody
                });
            });
        });

        proxyReq.on('error', function(err) {
            reject(err);
        });

        proxyReq.on('timeout', function() {
            proxyReq.destroy();
            reject(new Error('代理请求超时 (120s)'));
        });

        if (body) {
            proxyReq.write(body);
        }
        proxyReq.end();
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

function runMinimalClaude(prompt, timeoutMs, switchCommand, onAnthropicModel, onCliLog) {
    return new Promise(function(resolve, reject) {
        const scriptPath = path.join(__dirname, 'minimal-claude.js');
        let child;

        if (process.platform === 'win32' && String(switchCommand || '').trim()) {
            let cmd;
            try {
                cmd = sanitizeSwitchCommand(switchCommand);
            } catch (e) {
                reject(e);
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
                cwd: __dirname,
                stdio: ['ignore', 'pipe', 'pipe'],
                env: Object.assign({}, process.env, {
                    CATCHAT_PROMPT_B64: Buffer.from(String(prompt || ''), 'utf-8').toString('base64')
                })
            });
        } else {
            const args = [scriptPath, prompt];
            child = spawn(process.execPath, args, {
                cwd: __dirname,
                stdio: ['ignore', 'pipe', 'pipe']
            });
        }

        let stdout = '';
        let stderr = '';
        let settled = false;
        let stdoutLineBuffer = '';
        let stderrLineBuffer = '';
        let emittedAnthropicModel = false;
        let scriptFinishSeen = false;
        let forceCloseTimer = null;

        const timer = setTimeout(function() {
            if (settled) return;
            settled = true;
            try { child.kill(); } catch (_) {}
            reject(new Error('Claude CLI 调用超时（' + timeoutMs + 'ms）'));
        }, timeoutMs);

        child.stdout.on('data', function(chunk) {
            const text = chunk.toString('utf-8');
            stdout += text;

            // Stream stdout logs for live progress observation.
            // Some CLI status lines use '\r' (carriage return) without '\n'.
            stdoutLineBuffer += text;
            const lines = stdoutLineBuffer.split(/\r\n|\n|\r/);
            stdoutLineBuffer = lines.pop() || '';
            lines.forEach(function(line) {
                const t = String(line || '').trim();
                if (!scriptFinishSeen && (t.indexOf('Claude process exited with code:') === 0 || t.indexOf('Claude process ended by signal:') === 0)) {
                    scriptFinishSeen = true;
                    // Fallback: if wrapper shell lingers, force-close it shortly after script completion.
                    forceCloseTimer = setTimeout(function() {
                        if (settled) return;
                        try { child.kill(); } catch (_) {}
                    }, 5000);
                }
                if (typeof onCliLog === 'function' && String(line || '').trim()) {
                    onCliLog('stdout', line);
                }
            });
        });

        child.stderr.on('data', function(chunk) {
            const text = chunk.toString('utf-8');
            stderr += text;

            // Stream parse stderr lines so model info is visible immediately.
            stderrLineBuffer += text;
            const lines = stderrLineBuffer.split(/\r\n|\n|\r/);
            stderrLineBuffer = lines.pop() || '';
            lines.forEach(function(line) {
                const model = extractAnthropicModel(line);
                if (!emittedAnthropicModel && model !== null) {
                    emittedAnthropicModel = true;
                    if (typeof onAnthropicModel === 'function') {
                        onAnthropicModel(model);
                    }
                }
                if (typeof onCliLog === 'function' && String(line || '').trim()) {
                    onCliLog('stderr', line);
                }
            });
        });

        child.on('error', function(err) {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            reject(err);
        });

        child.on('close', function(code) {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            if (forceCloseTimer) {
                clearTimeout(forceCloseTimer);
                forceCloseTimer = null;
            }

            // Handle trailing stderr fragment without newline.
            if (!emittedAnthropicModel && stderrLineBuffer) {
                const model = extractAnthropicModel(stderrLineBuffer);
                if (model !== null && typeof onAnthropicModel === 'function') {
                    emittedAnthropicModel = true;
                    onAnthropicModel(model);
                }
            }
            if (stdoutLineBuffer && typeof onCliLog === 'function') {
                const trailingStdout = String(stdoutLineBuffer || '').trim();
                if (trailingStdout) onCliLog('stdout', trailingStdout);
            }
            if (stderrLineBuffer && typeof onCliLog === 'function') {
                const trailingStderr = String(stderrLineBuffer || '').trim();
                if (trailingStderr) onCliLog('stderr', trailingStderr);
            }

            const lines = String(stdout || '').split(/\r?\n/);
            const filtered = lines.filter(function(line) {
                const t = String(line || '').trim();
                if (!t) return false;
                if (t.indexOf('[No assistant text found in stream output]') === 0) return false;
                if (t.indexOf('Claude process exited with code:') === 0) return false;
                if (t.indexOf('Claude process ended by signal:') === 0) return false;
                if (/^The Claude Code environment has been switched to\s*\[/i.test(t)) return false;
                return true;
            });
            const replyText = filtered.join('\n').trim();

            if (code === 0 && replyText) {
                resolve({ replyText: replyText, stderrText: String(stderr || '').trim() });
                return;
            }

            const errMsg = String(stderr || '').trim() || ('Claude CLI 返回异常，退出码: ' + code);
            reject(new Error(errMsg));
        });
    });
}

function extractCurrentUserQuestion(prompt) {
    const raw = String(prompt || '');
    const marker = '【当前用户问题】';
    const idx = raw.indexOf(marker);
    if (idx < 0) return '';
    const rest = raw.slice(idx + marker.length).trim();
    if (!rest) return '';
    const endMarker = '\n\n【';
    const endIdx = rest.indexOf(endMarker);
    const block = (endIdx >= 0 ? rest.slice(0, endIdx) : rest).trim();
    return block;
}

function extractTaskInput(prompt) {
    const raw = String(prompt || '');
    const marker = '【任务输入】';
    const idx = raw.indexOf(marker);
    if (idx < 0) return '';
    const rest = raw.slice(idx + marker.length).trim();
    if (!rest) return '';
    const endMarker = '\n\n输出要求';
    const endIdx = rest.indexOf(endMarker);
    const block = (endIdx >= 0 ? rest.slice(0, endIdx) : rest).trim();
    if (!block) return '';
    const firstLine = block.split(/\r?\n/).find(function(line) {
        return String(line || '').trim();
    }) || '';
    return String(firstLine).trim();
}

function extractAnthropicModel(text) {
    const raw = String(text || '');
    const m = raw.match(/(?:^|\r?\n)ANTHROPIC_MODEL=([^\r\n]*)/);
    if (!m) return null;
    return String(m[1] || '').trim();
}

function resolvePipelineOutputDir(raw) {
    const input = String(raw || '').trim();
    if (!input) return path.resolve(__dirname, 'pipeline_outputs');
    return path.isAbsolute(input) ? input : path.resolve(__dirname, input);
}

function fileActionForWrite(filePath) {
    return fs.existsSync(filePath) ? 'updated' : 'created';
}

function writeTextFile(baseDir, relPath, content) {
    const absPath = path.join(baseDir, relPath);
    const action = fileActionForWrite(absPath);
    fs.mkdirSync(path.dirname(absPath), { recursive: true });
    fs.writeFileSync(absPath, String(content || ''), 'utf-8');
    return { action, path: relPath.replace(/\\/g, '/') };
}

function codeExtByLang(lang) {
    const key = String(lang || '').trim().toLowerCase();
    const map = {
        js: 'js', javascript: 'js', ts: 'ts', typescript: 'ts',
        jsx: 'jsx', tsx: 'tsx', py: 'py', python: 'py',
        java: 'java', go: 'go', rust: 'rs', cpp: 'cpp', c: 'c',
        csharp: 'cs', cs: 'cs', ruby: 'rb', php: 'php',
        sh: 'sh', bash: 'sh', powershell: 'ps1', ps1: 'ps1',
        yaml: 'yaml', yml: 'yml', json: 'json', html: 'html', css: 'css',
        markdown: 'md', md: 'md', sql: 'sql'
    };
    return map[key] || 'txt';
}

function extractCodeBlocks(markdown) {
    const blocks = [];
    const raw = String(markdown || '');
    const re = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
    let m;
    while ((m = re.exec(raw)) !== null) {
        blocks.push({ lang: String(m[1] || '').trim(), code: String(m[2] || '').replace(/\n$/, '') });
    }
    return blocks;
}

function persistPipelineArtifacts(phase, outputDirRaw, replyText) {
    const phaseKey = String(phase || 'chat').toLowerCase();
    const baseDir = resolvePipelineOutputDir(outputDirRaw);
    const files = [];
    const phaseFolderMap = { dev: 'design', review: 'review', test: 'test', chat: 'chat' };
    const phaseFolder = phaseFolderMap[phaseKey] || 'misc';

    fs.mkdirSync(baseDir, { recursive: true });
    files.push(writeTextFile(baseDir, path.join(phaseFolder, phaseKey + '_report.md'), replyText));

    const codeBlocks = extractCodeBlocks(replyText);
    codeBlocks.forEach(function(block, idx) {
        const ext = codeExtByLang(block.lang);
        const rel = path.join('code', phaseKey + '_' + String(idx + 1) + '.' + ext);
        files.push(writeTextFile(baseDir, rel, block.code));
    });

    const summary = '已落盘到 ' + baseDir + '，共写入 ' + files.length + ' 个文件。';
    return { summary, files, outputDir: baseDir };
}

// ====================== Request Counter ======================
let requestCount = 0;

// ====================== HTTP Server ======================
const server = http.createServer(function(req, res) {
    setCorsHeaders(res);

    // Handle CORS preflight
    if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
    }

    // Health check endpoint
    if (req.url === '/health' || req.url === '/ping') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok', name: 'CatChat CLI Proxy', version: '1.0.0', uptime: process.uptime(), requests: requestCount }));
        return;
    }

    // Proxy endpoint: /proxy
    if (req.url === '/proxy' && req.method === 'POST') {
        let body = '';
        req.on('data', function(chunk) { body += chunk; });
        req.on('end', function() {
            requestCount++;
            const reqId = requestCount;

            let payload;
            try {
                payload = JSON.parse(body);
            } catch (e) {
                log('❌', C.red, 'PARSE ERROR', '无法解析请求体');
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: '无法解析请求体 JSON' }));
                return;
            }

            const targetUrl = payload.targetUrl;
            const targetMethod = payload.method || 'POST';
            const targetHeaders = payload.headers || {};
            const targetBody = payload.body ? (typeof payload.body === 'string' ? payload.body : JSON.stringify(payload.body)) : '';

            if (!targetUrl) {
                log('❌', C.red, 'ERROR', '缺少 targetUrl');
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: '缺少 targetUrl 参数' }));
                return;
            }

            // Extract model & cat name from body for logging
            let modelName = '';
            let msgPreview = '';
            try {
                const b = typeof payload.body === 'string' ? JSON.parse(payload.body) : payload.body;
                modelName = b.model || '';
                if (b.messages && b.messages.length > 0) {
                    const lastMsg = b.messages[b.messages.length - 1];
                    msgPreview = (lastMsg.content || '').substring(0, 60);
                    if ((lastMsg.content || '').length > 60) msgPreview += '...';
                }
            } catch(e) {}

            log('📤', C.cyan, `REQUEST #${reqId}`, `→ ${C.bold}${targetUrl}${C.reset}`);
            if (modelName) log('  ', C.dim, '  模型', modelName);
            if (msgPreview) log('  ', C.dim, '  消息', msgPreview);

            const startTime = Date.now();

            proxyRequest(targetUrl, targetMethod, targetHeaders, targetBody)
                .then(function(proxyRes) {
                    const elapsed = Date.now() - startTime;
                    const statusColor = proxyRes.statusCode < 400 ? C.green : C.red;

                    // Extract reply preview
                    let replyPreview = '';
                    try {
                        const rd = JSON.parse(proxyRes.body);
                        if (rd.choices && rd.choices[0] && rd.choices[0].message) {
                            replyPreview = (rd.choices[0].message.content || '').substring(0, 80);
                            if ((rd.choices[0].message.content || '').length > 80) replyPreview += '...';
                        } else if (rd.content && Array.isArray(rd.content)) {
                            const txt = rd.content.filter(function(b) { return b.type === 'text'; }).map(function(b) { return b.text; }).join('');
                            replyPreview = txt.substring(0, 80);
                            if (txt.length > 80) replyPreview += '...';
                        }
                    } catch(e) {}

                    log('📥', statusColor, `RESPONSE #${reqId}`, `← ${C.bold}${proxyRes.statusCode}${C.reset} (${elapsed}ms)`);
                    if (replyPreview) log('  ', C.dim, '  回复', replyPreview);
                    console.log('');

                    // Forward response headers selectively
                    const fwdHeaders = { 'Content-Type': proxyRes.headers['content-type'] || 'application/json' };
                    res.writeHead(proxyRes.statusCode, fwdHeaders);
                    res.end(proxyRes.body);
                })
                .catch(function(err) {
                    const elapsed = Date.now() - startTime;
                    log('❌', C.red, `ERROR #${reqId}`, `${err.message} (${elapsed}ms)`);
                    console.log('');
                    res.writeHead(502, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ error: '代理请求失败: ' + err.message }));
                });
        });
        return;
    }

    // Claude Code endpoint: /claude-code
    if (req.url === '/claude-code' && req.method === 'POST') {
        let body = '';
        req.on('data', function(chunk) { body += chunk; });
        req.on('end', function() {
            requestCount++;
            const reqId = requestCount;

            let payload;
            try {
                payload = JSON.parse(body || '{}');
            } catch (e) {
                log('❌', C.red, 'PARSE ERROR', 'claude-code 请求体不是合法 JSON');
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: '无法解析请求体 JSON' }));
                return;
            }

            const prompt = String(payload.prompt || '').trim();
            const timeoutMsRaw = Number(payload.timeoutMs || 240000);
            const timeoutMs = Number.isFinite(timeoutMsRaw) ? Math.max(10000, Math.min(3600000, timeoutMsRaw)) : 240000;
            const source = String(payload.source || '').trim();
            const phase = String(payload.phase || '').trim();
            const catName = String(payload.catName || '').trim();
            const taskPreview = String(payload.taskPreview || '').trim();
            const outputDir = String(payload.outputDir || '').trim();
            const switchCommand = String(payload.switchCommand || '').trim();

            if (!prompt) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: '缺少 prompt 参数' }));
                return;
            }

            log('🤖', C.magenta, `CLAUDE #${reqId}`, '执行 minimal-claude.js');
            if (source || phase || catName) {
                log('  ', C.dim, '  路由', (source || '-') + ' / ' + (phase || '-') + ' / ' + (catName || '-'));
            }
            if (switchCommand) {
                log('  ', C.dim, '  模型切换', switchCommand);
            }
            const currentQuestion = extractCurrentUserQuestion(prompt);
            const taskFromPrompt = extractTaskInput(prompt);
            if (currentQuestion) {
                log('  ', C.dim, '  当前问题', currentQuestion.substring(0, 120) + (currentQuestion.length > 120 ? '...' : ''));
            } else if (taskPreview) {
                log('  ', C.dim, '  任务摘要', taskPreview.substring(0, 180) + (taskPreview.length > 180 ? '...' : ''));
            } else if (taskFromPrompt) {
                log('  ', C.dim, '  任务摘要', taskFromPrompt.substring(0, 180) + (taskFromPrompt.length > 180 ? '...' : ''));
            } else {
                log('  ', C.dim, '  提示词', prompt.substring(0, 80) + (prompt.length > 80 ? '...' : ''));
            }

            const startTime = Date.now();
            runMinimalClaude(prompt, timeoutMs, switchCommand, function(model) {
                log('  ', C.dim, '  ANTHROPIC_MODEL', model || '(empty)');
            }, function(stream, line) {
                const channel = stream === 'stderr' ? 'CLI[err]' : 'CLI[out]';
                log('  ', C.dim, '  ' + channel, String(line || '').slice(0, 400));
            })
                .then(function(result) {
                    const elapsed = Date.now() - startTime;
                    let pipeline = null;
                    if (source === 'pipeline') {
                        try {
                            pipeline = persistPipelineArtifacts(phase, outputDir, result.replyText);
                            log('  ', C.dim, '  文件', String(pipeline.files.length) + ' 个已写入');
                        } catch (e) {
                            log('❌', C.red, `CLAUDE #${reqId}`, '写文件失败: ' + e.message);
                            throw e;
                        }
                    }
                    log('✅', C.green, `CLAUDE #${reqId}`, `完成 (${elapsed}ms)`);
                    console.log('');
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({
                        ok: true,
                        reply: result.replyText,
                        stderr: result.stderrText || '',
                        pipeline: pipeline
                    }));
                })
                .catch(function(err) {
                    const elapsed = Date.now() - startTime;
                    log('❌', C.red, `CLAUDE #${reqId}`, `${err.message} (${elapsed}ms)`);
                    console.log('');
                    res.writeHead(502, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ ok: false, error: String(err && err.message || 'Claude CLI 执行失败') }));
                });
        });
        return;
    }

    // 404 for everything else
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not Found. 请使用 POST /proxy 端点。' }));
});

// ====================== Start ======================
server.listen(PORT, function() {
    console.log('');
    console.log(`${C.bgBlue}${C.white}${C.bold}                                        ${C.reset}`);
    console.log(`${C.bgBlue}${C.white}${C.bold}   🐱 CatChat CLI 代理服务器 v1.0.0     ${C.reset}`);
    console.log(`${C.bgBlue}${C.white}${C.bold}                                        ${C.reset}`);
    console.log('');
    console.log(`  ${C.green}✓${C.reset} 服务已启动: ${C.bold}${C.cyan}http://localhost:${PORT}${C.reset}`);
    console.log(`  ${C.green}✓${C.reset} 代理端点:   ${C.bold}POST http://localhost:${PORT}/proxy${C.reset}`);
    console.log(`  ${C.green}✓${C.reset} Claude端点:  ${C.bold}POST http://localhost:${PORT}/claude-code${C.reset}`);
    console.log(`  ${C.green}✓${C.reset} 健康检查:   ${C.bold}GET  http://localhost:${PORT}/health${C.reset}`);
    console.log('');
    console.log(`  ${C.yellow}📋 使用方法:${C.reset}`);
    console.log(`     1. 打开 CatChat 网页`);
    console.log(`     2. 在侧边栏找到「本地 CLI 代理」设置`);
    console.log(`     3. 填入地址: ${C.cyan}http://localhost:${PORT}${C.reset}`);
    console.log(`     4. 开启代理开关`);
    console.log(`     5. 所有请求将通过本地代理转发，无 CORS 限制`);
    console.log('');
    console.log(`  ${C.dim}按 Ctrl+C 停止服务${C.reset}`);
    console.log(`${C.dim}${'─'.repeat(50)}${C.reset}`);
    console.log('');
});

server.on('error', function(err) {
    if (err.code === 'EADDRINUSE') {
        console.error(`\n${C.red}❌ 端口 ${PORT} 已被占用！请使用 --port 指定其他端口。${C.reset}\n`);
    } else {
        console.error(`\n${C.red}❌ 服务器错误: ${err.message}${C.reset}\n`);
    }
    process.exit(1);
});

// Graceful shutdown
process.on('SIGINT', function() {
    console.log(`\n\n${C.yellow}👋 CatChat CLI 代理已停止。再见喵～${C.reset}\n`);
    process.exit(0);
});
