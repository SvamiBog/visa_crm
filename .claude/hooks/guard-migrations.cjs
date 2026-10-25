#!/usr/bin/env node
// PreToolUse hook (Edit|Write|MultiEdit): blocks changes to a migration that is already
// merged. CLAUDE.md → «Как работать»: migrations are not edited after merge; a schema
// change is a new revision. A migration counts as merged when the file is in origin/main
// (or in main when there is no origin/main). New migrations and the ones of the current
// branch pass. Writes through Bash are not seen here: review and CI are the last line.
//
// It also blocks a change that adds op.create_table( to a migration which turns on
// Row-Level Security for nothing (CLAUDE.md → «Мультиарендность»): no enable_tenant_rls /
// enable_shared_rls call and no ENABLE ROW LEVEL SECURITY outside those helpers' own
// definitions. tests/test_tenancy.py checks RLS on every table with tenant_id in CI.
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

/** Text the call adds: the Write content or every Edit/MultiEdit new_string. */
function addedText(toolInput) {
  if (typeof toolInput.content === "string") return toolInput.content;
  const edits = Array.isArray(toolInput.edits) ? toolInput.edits : [toolInput];
  return edits
    .map((edit) => (typeof edit.new_string === "string" ? edit.new_string : ""))
    .join("\n");
}

/** The file as it would be after the call. */
function resultingText(file, toolInput) {
  if (typeof toolInput.content === "string") return toolInput.content;
  let text = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
  const edits = Array.isArray(toolInput.edits) ? toolInput.edits : [toolInput];
  for (const edit of edits) {
    if (typeof edit.old_string !== "string" || typeof edit.new_string !== "string") continue;
    text = edit.replace_all
      ? text.split(edit.old_string).join(edit.new_string)
      : text.replace(edit.old_string, () => edit.new_string);
  }
  return text;
}

/** RLS is turned on somewhere other than inside the helpers' own definitions. */
function turnsOnRls(text) {
  const kept = [];
  let inHelper = false;
  for (const line of text.split(/\r?\n/)) {
    if (/^def enable_\w*rls\s*\(/.test(line)) {
      inHelper = true;
      continue;
    }
    if (inHelper && line.trim() !== "" && !/^\s/.test(line)) inHelper = false;
    if (!inHelper) kept.push(line);
  }
  return /enable_(tenant|shared)_rls\s*\(|ENABLE ROW LEVEL SECURITY/.test(kept.join("\n"));
}

/** 2 when the change adds a table to a migration that never turns on RLS, else 0. */
function checkRls(file, repoPath, toolInput) {
  if (!/op\.create_table\s*\(/.test(addedText(toolInput))) return 0;
  if (turnsOnRls(resultingText(file, toolInput))) return 0;
  process.stderr.write(
    `${repoPath} creates a table but turns on no Row-Level Security (CLAUDE.md → ` +
      "«Мультиарендность»). A specialist's table needs tenant_id and enable_tenant_rls(<table>) " +
      "— the pattern is in migration 0001; shared reference data uses enable_shared_rls " +
      "(migration 0009). Add the call in the same change (skill ecc-database-migrations).\n",
  );
  return 2;
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
  const rls = () => checkRls(path.resolve(projectDir, file), repoPath, input.tool_input);

  for (const base of BASES) {
    const ref = git(projectDir, ["rev-parse", "--verify", "--quiet", `${base}^{commit}`]);
    if (ref.error) {
      process.stderr.write(`[guard-migrations] git unavailable (${ref.error.message}); not checked\n`);
      return rls();
    }
    if (ref.status !== 0) continue; // no such ref here: try the next one
    const merged = git(projectDir, ["cat-file", "-e", `${base}:${repoPath}`]);
    if (merged.status !== 0) return rls(); // a new migration or one of this branch
    process.stderr.write(
      `${repoPath} is already merged (it is in ${base}): migrations are not edited after ` +
        "merge (CLAUDE.md → «Как работать»). Change the schema with a new revision instead: " +
        'cd backend && uv run alembic revision --autogenerate --rev-id 00NN -m "…", then review ' +
        "it (skill ecc-database-migrations).\n",
    );
    return 2;
  }
  process.stderr.write("[guard-migrations] neither origin/main nor main found; not checked\n");
  return rls();
}

let code = 0;
try {
  code = main();
} catch (error) {
  process.stderr.write(`[guard-migrations] ${error.message}; not checked\n`);
}
process.exit(code);
