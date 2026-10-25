#!/usr/bin/env node
// PostToolUse hook (Edit|Write|MultiEdit): formats an edited backend Python file with
// `ruff format`, using the project's settings in backend/pyproject.toml. It never
// blocks: it always exits 0, and a file ruff cannot format (a syntax error) stays as is.
//
// ruff is RUFF_EXE if set, else the one in backend/.venv (`uv sync`), else from PATH.
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

function ruffPath(projectDir) {
  if (process.env.RUFF_EXE) return process.env.RUFF_EXE;
  const venv =
    process.platform === "win32"
      ? path.join(projectDir, "backend", ".venv", "Scripts", "ruff.exe")
      : path.join(projectDir, "backend", ".venv", "bin", "ruff");
  return fs.existsSync(venv) ? venv : "ruff";
}

function main() {
  let input;
  try {
    input = JSON.parse(fs.readFileSync(0, "utf8"));
  } catch {
    return; // not a hook payload: nothing to do
  }
  const file = input && input.tool_input && input.tool_input.file_path;
  if (typeof file !== "string" || !file.endsWith(".py")) return;

  const projectDir = process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd();
  const backend = path.join(projectDir, "backend");
  const target = path.resolve(projectDir, file);
  const rel = path.relative(backend, target);
  // Only backend files, never the virtualenv or caches.
  if (rel.startsWith("..") || path.isAbsolute(rel)) return;
  if (rel.split(path.sep).some((part) => part === ".venv" || part === "__pycache__")) return;
  if (!fs.existsSync(target)) return;

  const result = spawnSync(ruffPath(projectDir), ["format", "--force-exclude", target], {
    cwd: backend,
    encoding: "utf8",
    timeout: 20000,
  });
  if (result.error || result.status !== 0) {
    const reason = result.error
      ? result.error.message
      : (result.stderr || "").trim().split("\n")[0].replace(target, rel);
    process.stderr.write(`[format-python] ${rel} left as is: ${reason}\n`);
  }
}

try {
  main();
} catch (error) {
  process.stderr.write(`[format-python] ${error.message}\n`);
}
process.exit(0);
