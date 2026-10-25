#!/usr/bin/env node
// PostToolUse hook: after Claude edits a Python file under backend/, sorts its
// imports and formats it with ruff — what `uv run ruff check . && uv run ruff
// format .` in CLAUDE.md does, only earlier (CI checks the import order with
// `ruff check`). Reads the hook payload from stdin and never blocks: exit code 0.
//
// Ruff comes from RUFF_EXE when it is set (for example in
// .claude/settings.local.json), otherwise from backend/.venv (the version pinned
// in uv.lock, after `uv sync`), otherwise from PATH.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const backendDir = path.join(projectDir, 'backend');

/** A ruff executable to run without a shell, or null. */
function resolveRuff() {
  if (process.env.RUFF_EXE) return process.env.RUFF_EXE;
  const venv =
    process.platform === 'win32'
      ? path.join(backendDir, '.venv', 'Scripts', 'ruff.exe')
      : path.join(backendDir, '.venv', 'bin', 'ruff');
  if (fs.existsSync(venv)) return venv;
  if (process.platform !== 'win32') return 'ruff';
  // Without a shell only a real .exe starts: take it from `where`, skip .cmd/.bat shims.
  const where = spawnSync('where', ['ruff'], { encoding: 'utf8' });
  const exe = (where.stdout || '').split(/\r?\n/).find((line) => /\.exe$/i.test(line));
  return exe || null;
}

/** The edited file relative to backend/ with forward slashes, or null. */
function backendPython(file) {
  if (!file) return null;
  const rel = path.relative(backendDir, path.resolve(projectDir, file)).replace(/\\/g, '/');
  if (rel.startsWith('..') || path.isAbsolute(rel)) return null;
  if (!rel.endsWith('.py') || rel.startsWith('.venv/')) return null;
  return rel;
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  input += chunk;
});
process.stdin.on('end', () => {
  let file;
  try {
    file = JSON.parse(input).tool_input?.file_path;
  } catch {
    return;
  }
  const rel = backendPython(file);
  if (!rel || !fs.existsSync(path.join(backendDir, rel))) return;
  const ruff = resolveRuff();
  if (!ruff) {
    process.stderr.write(
      '[ruff-format] ruff не найден: задайте RUFF_EXE или выполните `uv sync` в backend\n',
    );
    return;
  }
  // From backend/, so ruff reads backend/pyproject.toml (line length, rules).
  const steps = [
    ['check', '--select', 'I', '--fix', '--quiet', rel],
    ['format', '--quiet', rel],
  ];
  for (const args of steps) {
    const result = spawnSync(ruff, args, { cwd: backendDir, encoding: 'utf8', timeout: 20000 });
    if (result.error) {
      process.stderr.write(`[ruff-format] ${ruff}: ${result.error.message}\n`);
      return;
    }
    // A syntax error mid-edit is fine: the next edit fixes it, ruff check reports the rest.
    if (result.status !== 0) {
      process.stderr.write(`[ruff-format] ${(result.stderr || result.stdout).trim()}\n`);
      return;
    }
  }
});
