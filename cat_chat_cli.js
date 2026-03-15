#!/usr/bin/env node
// ============================================================
//  🐱 CatChat CLI — 本地代理服务器
//  解决浏览器 CORS 跨域限制，代理转发 API 请求
//  启动方式：node cat_chat_cli.js [--port 3456]
// ============================================================

const http = require('http');
const https = require('https');
const net = require('net');
const url = require('url');
const path = require('path');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');

// ====================== Config ======================
const DEFAULT_PORT = 3456;
const PROXY_TIMEOUT_MS = 3600000;
let PORT = DEFAULT_PORT;
const MANAGED_ENV_KEYS = new Set();
let activeSwitchCommand = '';

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
            timeout: PROXY_TIMEOUT_MS
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
            reject(new Error('代理请求超时 (3600s)'));
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

function applySwitchCommandToProcessEnv(switchCommand) {
    if (process.platform !== 'win32') {
        return { applied: false, switchCommand: '', anthropicModel: String(process.env.ANTHROPIC_MODEL || ''), keys: [] };
    }

    const cmd = sanitizeSwitchCommand(switchCommand || '');
    if (!cmd) {
        activeSwitchCommand = '';
        return { applied: false, switchCommand: '', anthropicModel: String(process.env.ANTHROPIC_MODEL || ''), keys: [] };
    }

    const psScript = [
        "$ErrorActionPreference='Stop'",
        'if (Test-Path $PROFILE) { . $PROFILE }',
        cmd,
        "$allow = @('ANTHROPIC_*','CLAUDE_*')",
        "Get-ChildItem Env: | Where-Object { $name = $_.Name; ($allow | Where-Object { $name -like $_ }) } | ForEach-Object { Write-Output ('ENVKV:' + $_.Name + '=' + $_.Value) }"
    ].join('; ');

    const ret = spawnSync('powershell.exe', ['-NoLogo', '-NoProfile', '-Command', psScript], {
        cwd: __dirname,
        encoding: 'utf8',
        timeout: 120000
    });

    if (ret.error) {
        throw ret.error;
    }
    if (ret.status !== 0) {
        throw new Error(String(ret.stderr || ret.stdout || ('模型切换命令执行失败，退出码: ' + ret.status)).trim());
    }

    const output = String(ret.stdout || '');
    const kv = {};
    output.split(/\r?\n/).forEach(function(line) {
        const raw = String(line || '').trim();
        if (!raw || raw.indexOf('ENVKV:') !== 0) return;
        const pair = raw.slice('ENVKV:'.length);
        const idx = pair.indexOf('=');
        if (idx <= 0) return;
        const key = pair.slice(0, idx).trim();
        const value = pair.slice(idx + 1);
        if (!key) return;
        kv[key] = value;
    });

    MANAGED_ENV_KEYS.forEach(function(key) {
        if (!Object.prototype.hasOwnProperty.call(kv, key)) {
            delete process.env[key];
            MANAGED_ENV_KEYS.delete(key);
        }
    });

    Object.keys(kv).forEach(function(key) {
        process.env[key] = kv[key];
        MANAGED_ENV_KEYS.add(key);
    });

    activeSwitchCommand = cmd;
    return {
        applied: true,
        switchCommand: cmd,
        anthropicModel: String(process.env.ANTHROPIC_MODEL || ''),
        keys: Object.keys(kv)
    };
}

function spawnMinimalClaudeServer(port, switchCommand) {
    const scriptPath = path.join(__dirname, 'minimal-claude.js');
    const switchCmd = String(switchCommand || '').trim();

    if (process.platform === 'win32') {
        return new Promise(function(resolve, reject) {
            let safeSwitch = '';
            try {
                safeSwitch = switchCmd ? sanitizeSwitchCommand(switchCmd) : '';
            } catch (e) {
                reject(e);
                return;
            }

            const nodeExe = String(process.execPath || 'node').replace(/'/g, "''");
            const scriptEscaped = String(scriptPath || '').replace(/'/g, "''");
            const cwdEscaped = String(__dirname || '').replace(/'/g, "''");
            const psParts = [
                "$ErrorActionPreference='Stop'",
                'if (Test-Path $PROFILE) { . $PROFILE }'
            ];
            if (safeSwitch) psParts.push(safeSwitch);
            psParts.push("$p = Start-Process -FilePath '" + nodeExe + "' -ArgumentList @('" + scriptEscaped + "','--port','" + String(port) + "') -WorkingDirectory '" + cwdEscaped + "' -PassThru");
            psParts.push("Write-Output ('AGENT_PID=' + $p.Id)");

            const launcher = spawn('powershell.exe', ['-NoLogo', '-NoProfile', '-Command', psParts.join('; ')], {
                cwd: __dirname,
                stdio: ['ignore', 'pipe', 'pipe'],
                env: process.env
            });

            let out = '';
            let err = '';
            launcher.stdout.on('data', function(chunk) { out += String(chunk || ''); });
            launcher.stderr.on('data', function(chunk) { err += String(chunk || ''); });
            launcher.on('error', reject);
            launcher.on('close', function(code) {
                const m = String(out || '').match(/AGENT_PID=(\d+)/);
                const pid = m ? parseInt(m[1], 10) : NaN;
                if (code === 0 && Number.isFinite(pid) && pid > 0) {
                    resolve({ pid: pid, child: null, mode: 'start-process' });
                    return;
                }
                reject(new Error(String(err || out || ('Start-Process 启动失败，退出码: ' + code)).trim()));
            });
        });
    }

    return Promise.resolve({
        pid: null,
        mode: 'spawn',
        child: spawn(process.execPath, [scriptPath, '--port', String(port)], {
            cwd: __dirname,
            stdio: ['ignore', 'pipe', 'pipe'],
            env: process.env
        })
    });
}

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

function runMinimalClaude(prompt, timeoutMs, switchCommand, workingDir, onAnthropicModel, onCliLog) {
    return new Promise(function(resolve, reject) {
        const scriptPath = path.join(__dirname, 'minimal-claude.js');
        let effectiveWorkDir;
        try {
            effectiveWorkDir = resolveWorkingDirectory(workingDir);
        } catch (e) {
            reject(e);
            return;
        }
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
                cwd: effectiveWorkDir,
                stdio: ['ignore', 'pipe', 'pipe'],
                env: Object.assign({}, process.env, {
                    CATCHAT_PROMPT_B64: Buffer.from(String(prompt || ''), 'utf-8').toString('base64'),
                    CATCHAT_WORKDIR: effectiveWorkDir
                })
            });
        } else {
            const args = [scriptPath, prompt];
            child = spawn(process.execPath, args, {
                cwd: effectiveWorkDir,
                stdio: ['ignore', 'pipe', 'pipe'],
                env: Object.assign({}, process.env, {
                    CATCHAT_WORKDIR: effectiveWorkDir
                })
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

const pipelineCliAgentsByCatId = new Map();

function sanitizeCliPort(raw) {
    const p = parseInt(raw, 10);
    if (!Number.isFinite(p) || p < 1024 || p > 65535) {
        throw new Error('CLI 端口非法，必须在 1024-65535 之间');
    }
    return p;
}

function isPortUsedByOtherAgent(port, currentCatId) {
    return Array.from(pipelineCliAgentsByCatId.values()).some(function(a) {
        return a.port === port && a.catId !== currentCatId && isProcessRunning(a);
    });
}

function checkPortFree(port) {
    return new Promise(function(resolve) {
        const server = net.createServer();
        server.once('error', function(err) {
            const code = String(err && err.code || '');
            if (code === 'EADDRINUSE' || code === 'EACCES') {
                resolve(false);
                return;
            }
            resolve(false);
        });
        server.once('listening', function() {
            server.close(function() { resolve(true); });
        });
        try {
            server.listen({ port: port, host: '127.0.0.1' });
        } catch (_) {
            resolve(false);
        }
    });
}

function resolveAvailablePort(startPort, reservedPorts, currentCatId) {
    const reserved = reservedPorts || new Set();
    const maxScan = 2048;
    let candidate = sanitizeCliPort(startPort);
    let scanned = 0;

    function next() {
        if (scanned >= maxScan || candidate > 65535) {
            return Promise.reject(new Error('未找到可用 CLI 端口（从 ' + startPort + ' 起扫描）'));
        }
        scanned++;

        if (reserved.has(candidate) || candidate === PORT || isPortUsedByOtherAgent(candidate, currentCatId)) {
            candidate++;
            return next();
        }
        return checkPortFree(candidate).then(function(free) {
            if (free) return candidate;
            candidate++;
            return next();
        });
    }

    return next();
}

function isProcessRunning(agent) {
    if (!agent) return false;
    if (Number.isFinite(agent.pid) && agent.pid > 0) {
        try {
            process.kill(agent.pid, 0);
            return true;
        } catch (_) {
            return false;
        }
    }
    const child = agent.child;
    return !!(child && !child.killed && child.exitCode == null);
}

function stopPipelineCliAgent(agent) {
    if (!agent) return;
    if (Number.isFinite(agent.pid) && agent.pid > 0 && process.platform === 'win32') {
        try {
            spawnSync('taskkill', ['/PID', String(agent.pid), '/T', '/F'], { stdio: 'ignore' });
        } catch (_) {}
        return;
    }
    if (agent.child) {
        try { agent.child.kill(); } catch (_) {}
    }
}

function waitForServerReady(port, timeoutMs) {
    const startedAt = Date.now();
    const intervalMs = 250;
    const maxWait = Math.max(2000, Number(timeoutMs || 12000));

    return new Promise(function(resolve, reject) {
        function probe() {
            const req = http.get({ hostname: '127.0.0.1', port: port, path: '/health', timeout: 1500 }, function(res) {
                let body = '';
                res.on('data', function(chunk) { body += chunk; });
                res.on('end', function() {
                    if (res.statusCode === 200) {
                        resolve(true);
                        return;
                    }
                    retryOrFail(new Error('CLI 健康检查失败: HTTP ' + res.statusCode));
                });
            });
            req.on('error', function(err) {
                retryOrFail(err);
            });
            req.on('timeout', function() {
                req.destroy(new Error('timeout'));
            });
        }

        function retryOrFail(lastErr) {
            if (Date.now() - startedAt >= maxWait) {
                reject(new Error('等待 CLI 端口 ' + port + ' 就绪超时: ' + String(lastErr && lastErr.message || 'unknown')));
                return;
            }
            setTimeout(probe, intervalMs);
        }

        probe();
    });
}

function ensurePipelineCliAgent(spec, reservedPorts) {
    const catId = String(spec && spec.catId || '').trim();
    const catName = String(spec && spec.catName || '').trim() || catId;
    const requestedPort = sanitizeCliPort(spec && spec.port);
    const autoResolve = spec && spec.autoResolvePortConflict !== false;
    const switchCommand = String(spec && spec.switchCommand || '').trim();

    if (!catId) {
        return Promise.reject(new Error('缺少 catId')); 
    }

    const current = pipelineCliAgentsByCatId.get(catId);
    const reserved = reservedPorts || new Set();

    return resolveAvailablePort(requestedPort, reserved, catId).then(function(port) {
        if (!autoResolve && port !== requestedPort) {
            throw new Error('端口 ' + requestedPort + ' 不可用，请更换');
        }

        if (current && current.port === port && isProcessRunning(current)) {
            if (String(current.switchCommand || '') !== switchCommand) {
                stopPipelineCliAgent(current);
                pipelineCliAgentsByCatId.delete(catId);
            } else {
                return {
                    catId: catId,
                    catName: catName,
                    port: port,
                    requestedPort: requestedPort,
                    reassigned: port !== requestedPort,
                    switchCommand: switchCommand,
                    pid: Number.isFinite(current.pid) ? current.pid : null,
                    mode: current.mode || 'spawn',
                    status: 'reused'
                };
            }
        }
        if (current && current.port !== port) {
            stopPipelineCliAgent(current);
            pipelineCliAgentsByCatId.delete(catId);
        }

        return spawnMinimalClaudeServer(port, switchCommand).then(function(launched) {
            const agent = {
                catId: catId,
                catName: catName,
                port: port,
                switchCommand: switchCommand,
                pid: Number.isFinite(launched && launched.pid) ? launched.pid : null,
                child: (launched && launched.child) || null,
                mode: String((launched && launched.mode) || 'spawn'),
                startedAt: new Date().toISOString()
            };
            pipelineCliAgentsByCatId.set(catId, agent);

            if (agent.child) {
                agent.child.stdout.on('data', function(chunk) {
                    const msg = String(chunk || '').trim();
                    if (!msg) return;
                    log('  ', C.dim, 'CAT-CLI[' + port + ']', msg.slice(0, 240));
                });
                agent.child.stderr.on('data', function(chunk) {
                    const msg = String(chunk || '').trim();
                    if (!msg) return;
                    log('  ', C.dim, 'CAT-CLI[' + port + '][err]', msg.slice(0, 240));
                });
                agent.child.on('exit', function(code, signal) {
                    const now = pipelineCliAgentsByCatId.get(catId);
                    if (now && now.child === agent.child) {
                        pipelineCliAgentsByCatId.delete(catId);
                    }
                    log('⚠️', C.yellow, 'CAT-CLI EXIT', catName + ' @' + port + ' code=' + code + ' signal=' + (signal || '-'));
                });
            } else {
                log('🪟', C.cyan, 'CAT-CLI WINDOW', catName + ' @' + port + ' PID=' + String(agent.pid || '-'));
            }

            return waitForServerReady(port, 15000).then(function() {
                return {
                    catId: catId,
                    catName: catName,
                    port: port,
                    requestedPort: requestedPort,
                    reassigned: port !== requestedPort,
                    switchCommand: switchCommand,
                    pid: agent.pid,
                    mode: agent.mode,
                    status: 'started'
                };
            }).catch(function(err) {
                stopPipelineCliAgent(agent);
                pipelineCliAgentsByCatId.delete(catId);
                throw err;
            });
        });
    });
}

function syncPipelineCliAgents(cats) {
    const list = Array.isArray(cats) ? cats : [];
    const desiredIds = {};
    list.forEach(function(item) {
        const cid = String(item && item.catId || '').trim();
        if (cid) desiredIds[cid] = true;
    });

    // Stop removed cats.
    Array.from(pipelineCliAgentsByCatId.values()).forEach(function(agent) {
        if (!desiredIds[agent.catId]) {
            stopPipelineCliAgent(agent);
            pipelineCliAgentsByCatId.delete(agent.catId);
        }
    });

    const reserved = new Set([PORT]);
    let chain = Promise.resolve([]);
    list.forEach(function(item) {
        chain = chain.then(function(results) {
            const spec = Object.assign({}, item, { autoResolvePortConflict: true });
            return ensurePipelineCliAgent(spec, reserved).then(function(info) {
                reserved.add(info.port);
                results.push(info);
                return results;
            });
        });
    });
    return chain;
}

function callDedicatedCliServer(port, payload) {
    const targetUrl = 'http://127.0.0.1:' + port + '/claude-code';
    const body = JSON.stringify(payload || {});
    return proxyRequest(targetUrl, 'POST', { 'Content-Type': 'application/json' }, body).then(function(result) {
        let data = {};
        try {
            data = JSON.parse(String(result && result.body || '{}'));
        } catch (_) {
            throw new Error('猫猫 CLI 返回了不可解析的响应');
        }
        if (!result || result.statusCode >= 400 || data.ok !== true) {
            throw new Error(String((data && data.error) || ('猫猫 CLI 调用失败，HTTP ' + (result && result.statusCode))));
        }
        return {
            replyText: String(data.reply || '').trim(),
            stderrText: String(data.stderr || '').trim()
        };
    });
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
        const agents = Array.from(pipelineCliAgentsByCatId.values()).map(function(a) {
            return {
                catId: a.catId,
                catName: a.catName,
                port: a.port,
                switchCommand: String(a.switchCommand || ''),
                pid: Number.isFinite(a.pid) ? a.pid : null,
                mode: a.mode || 'spawn',
                running: isProcessRunning(a)
            };
        });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
            status: 'ok',
            name: 'CatChat CLI Proxy',
            version: '1.0.0',
            uptime: process.uptime(),
            requests: requestCount,
            activeSwitchCommand: activeSwitchCommand,
            anthropicModel: String(process.env.ANTHROPIC_MODEL || ''),
            pipelineAgents: agents
        }));
        return;
    }

    if (req.url === '/set-switch' && req.method === 'POST') {
        let body = '';
        req.on('data', function(chunk) { body += chunk; });
        req.on('end', function() {
            let payload;
            try {
                payload = JSON.parse(body || '{}');
            } catch (_) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: false, error: '无法解析请求体 JSON' }));
                return;
            }

            const switchCommand = String(payload.switchCommand || '').trim();
            if (!switchCommand) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: false, error: '缺少 switchCommand 参数' }));
                return;
            }

            try {
                const result = applySwitchCommandToProcessEnv(switchCommand);
                log('🔀', C.magenta, 'SET SWITCH', switchCommand + ' -> ANTHROPIC_MODEL=' + String(result.anthropicModel || ''));
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: true, result: result }));
            } catch (err) {
                log('❌', C.red, 'SET SWITCH', String(err && err.message || 'failed'));
                res.writeHead(502, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: false, error: String(err && err.message || '模型切换失败') }));
            }
        });
        return;
    }

    if (req.url === '/pipeline/sync' && req.method === 'POST') {
        // Manual mode: user starts one CLI instance per cat port by themselves.
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
            ok: false,
            mode: 'manual',
            error: '当前为手动多窗口模式：请为每只猫手动启动独立实例，例如 node cat_chat_cli.js --port 3460'
        }));
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
            const catId = String(payload.catId || '').trim();
            const taskPreview = String(payload.taskPreview || '').trim();
            const outputDir = String(payload.outputDir || '').trim();
            const workingDir = String(payload.workingDir || outputDir || '').trim();
            const switchCommandRaw = String(payload.switchCommand || '').trim();
            const switchCommand = source === 'pipeline' ? '' : switchCommandRaw;
            let catCliPort = null;
            if (payload.catCliPort !== undefined && payload.catCliPort !== null && String(payload.catCliPort).trim() !== '') {
                try {
                    catCliPort = sanitizeCliPort(payload.catCliPort);
                } catch (e) {
                    res.writeHead(400, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ ok: false, error: e.message }));
                    return;
                }
            }

            if (!prompt) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: '缺少 prompt 参数' }));
                return;
            }

            log('🤖', C.magenta, `CLAUDE #${reqId}`, '执行 minimal-claude.js');
            if (source || phase || catName) {
                log('  ', C.dim, '  路由', (source || '-') + ' / ' + (phase || '-') + ' / ' + (catName || '-'));
            }
            if (catCliPort) {
                log('  ', C.dim, '  猫猫端口', String(catCliPort));
            }
            if (switchCommandRaw && source !== 'pipeline') {
                log('  ', C.dim, '  模型切换', switchCommand);
            }
            if (source === 'pipeline' && switchCommandRaw) {
                log('  ', C.dim, '  模型切换', '已忽略（手动多窗口模式下按窗口预设）');
            }
            if (workingDir) {
                log('  ', C.dim, '  工作目录', workingDir);
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
            const runPromise = runMinimalClaude(prompt, timeoutMs, switchCommand, workingDir, function(model) {
                    log('  ', C.dim, '  ANTHROPIC_MODEL', model || '(empty)');
                }, function(stream, line) {
                    const channel = stream === 'stderr' ? 'CLI[err]' : 'CLI[out]';
                    log('  ', C.dim, '  ' + channel, String(line || '').slice(0, 400));
                });

            runPromise
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
    res.end(JSON.stringify({ error: 'Not Found. 可用端点: POST /proxy, POST /claude-code, POST /pipeline/sync, GET /health' }));
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
    console.log(`  ${C.green}✓${C.reset} 流水线同步: ${C.bold}POST http://localhost:${PORT}/pipeline/sync${C.reset}`);
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
    Array.from(pipelineCliAgentsByCatId.values()).forEach(function(agent) {
        stopPipelineCliAgent(agent);
    });
    console.log(`\n\n${C.yellow}👋 CatChat CLI 代理已停止。再见喵～${C.reset}\n`);
    process.exit(0);
});
