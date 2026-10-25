#!/usr/bin/env node
// PreToolUse hook (Edit|Write|MultiEdit): blocks changes to a migration that is already
// merged. CLAUDE.md → «Как работать»: migrations are not edited after merge; a schema
// change is a new revision. A migration counts as merged when the file is in origin/main
// (or in main when there is no origin/main). New migrations and the ones of the current
// branch pass. Writes through Bash are not seen here: review and CI are the last line.
//
// Exit 2 blocks the tool call and shows stderr to Claude. If git cannot answer, the hook
// lets the change through with a warning rather than stopping work.
//
// git is GIT_EXE if set, else from PATH.
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const VERSIONS = ["backend", "migrations", "versions"];
const BASES = ["origin/main", "main"];

function git(projectDir, args) {
  return spawnSync(process.env.GIT_EXE || "git", ["-C", projectDir, ...args], {
    encoding: "utf8",
    timeout: 10000,
  });
}

function main() {
  let input;
  try {
    input = JSON.parse(fs.readFileSync(0, "utf8"));
  } catch {
    return 0; // not a hook payload
  }
  const file = input && input.tool_input && input.tool_input.file_path;
  if (typeof file !== "string" || !file.endsWith(".py")) return 0;

  const projectDir = process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd();
  const rel = path.relative(projectDir, path.resolve(projectDir, file));
  const parts = rel.split(path.sep);
  const inVersions =
    parts.length === VERSIONS.length + 1 && VERSIONS.every((part, i) => parts[i] === part);
  if (!inVersions) return 0;
  const repoPath = parts.join("/");

  for (const base of BASES) {
    const ref = git(projectDir, ["rev-parse", "--verify", "--quiet", `${base}^{commit}`]);
    if (ref.error) {
      process.stderr.write(`[guard-migrations] git unavailable (${ref.error.message}); not checked\n`);
      return 0;
    }
    if (ref.status !== 0) continue; // no such ref here: try the next one
    const merged = git(projectDir, ["cat-file", "-e", `${base}:${repoPath}`]);
    if (merged.status !== 0) return 0; // a new migration or one of this branch
    process.stderr.write(
      `${repoPath} is already merged (it is in ${base}): migrations are not edited after ` +
        "merge (CLAUDE.md → «Как работать»). Change the schema with a new revision instead: " +
        'cd backend && uv run alembic revision --autogenerate --rev-id 00NN -m "…", then review ' +
        "it (skill ecc-database-migrations).\n",
    );
    return 2;
  }
  process.stderr.write("[guard-migrations] neither origin/main nor main found; not checked\n");
  return 0;
}

let code = 0;
try {
  code = main();
} catch (error) {
  process.stderr.write(`[guard-migrations] ${error.message}; not checked\n`);
}
process.exit(code);
