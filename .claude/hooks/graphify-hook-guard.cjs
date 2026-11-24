#!/usr/bin/env node
// PreToolUse (Bash|Grep, Read|Glob) and SessionStart (compact) hook: hands the call to
// `graphify hook-guard <search|read|compact>`, which tells Claude what the code graph
// (graphify-out/graph.json, CLAUDE.md → «graphify») already knows before it greps or reads
// files, and says when the graph is stale.
//
// graphify runs only when this checkout has a graph. Most worktrees and cloud sessions have
// none, and starting graphify's Python costs about 0.4 s on every Bash, Grep, Read and Glob
// call; node with this check costs under 0.1 s. Only the default graphify-out/ is looked at:
// the project does not move it with GRAPHIFY_OUT or .graphifyrc.
//
// graphify comes from ~/.local/bin (where `uv tool install` puts it) or else from PATH, so
// the hook works before a restart picks up a new PATH. Without graphify the hook stays
// silent. graphify's output and exit code pass through unchanged.
//
// The file name holds "graphify" and "hook-guard", so `graphify install` and
// `graphify uninstall` recognise these entries in .claude/settings.json as their own.
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const MODES = new Set(["search", "read", "compact"]);
const TIMEOUT_MS = 9000; // below the 10 s hook timeout in .claude/settings.json

function graphifyExe() {
  const name = process.platform === "win32" ? "graphify.exe" : "graphify";
  const local = path.join(os.homedir(), ".local", "bin", name);
  return fs.existsSync(local) ? local : "graphify";
}

function main() {
  const mode = process.argv[2];
  if (!MODES.has(mode)) return 0;
  const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  if (!fs.existsSync(path.join(projectDir, "graphify-out", "graph.json"))) return 0;

  let input = "";
  try {
    input = fs.readFileSync(0, "utf8");
  } catch {
    // No hook payload on stdin: graphify gets an empty one and lets the call through.
  }
  const result = spawnSync(graphifyExe(), ["hook-guard", mode], {
    cwd: projectDir,
    input,
    stdio: ["pipe", "inherit", "inherit"],
    timeout: TIMEOUT_MS,
  });
  // Not installed or too slow: a hint is not worth stopping the tool call.
  if (result.error) return 0;
  return result.status ?? 0;
}

process.exit(main());
