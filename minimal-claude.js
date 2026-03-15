const { spawn } = require('child_process');
const readline = require('readline');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const useEnvPrompt = args.includes('--from-env-b64');
let prompt = args.filter((a) => a !== '--from-env-b64').join(' ').trim();
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
const claudeArgs = ['-p', prompt];
if (verboseEnabled) {
  // Claude CLI requires --verbose when using --print (-p) with stream-json output.
  claudeArgs.push('--output-format', 'stream-json', '--verbose');
}

// Print the effective model env for observability in the parent proxy logs.
console.error('ANTHROPIC_MODEL=' + String(process.env.ANTHROPIC_MODEL || ''));

if (!prompt) {
  console.error('Usage: node minimal-claude.js "<your prompt>"');
  process.exit(1);
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
  if (!isWindows) {
    return spawn(claudeBin, claudeArgs, {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  }

  // Prefer launching the Claude CLI JS entrypoint directly via node to avoid
  // cmd.exe argument parsing issues with multi-line prompts.
  if (!process.env.CLAUDE_BIN) {
    const cliJs = resolveWindowsClaudeCliEntrypoint();
    if (cliJs) {
      return spawn(process.execPath, [cliJs, ...claudeArgs], {
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    }
  }

  return spawn(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', claudeBin, ...claudeArgs], {
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
