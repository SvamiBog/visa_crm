#!/usr/bin/env node
// PreToolUse hook (VisaCRM): guards the migrations, where the multi-tenancy rule
// of CLAUDE.md («Мультиарендность с первого дня») is made. Blocks an
// Edit/Write/MultiEdit of backend/migrations/versions/*.py that
//   - changes a migration already merged into main («миграции не редактируем
//     после мержа»), or
//   - adds op.create_table( to a migration that turns on Row-Level Security for
//     nothing (no enable_tenant_rls / enable_shared_rls call, no
//     ENABLE ROW LEVEL SECURITY outside those helpers).
// Exit code 2 blocks the call and shows stderr to Claude; anything else passes.
// Writes through Bash are not seen here: tests/test_tenancy.py in CI still checks
// RLS on every table with tenant_id; nothing in CI checks merged migrations.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const VERSIONS = 'backend/migrations/versions/';

/** The file relative to the project with forward slashes, if it is a migration. */
function migrationPath(file) {
  if (!file) return null;
  const rel = path.relative(projectDir, path.resolve(projectDir, file)).replace(/\\/g, '/');
  if (!rel.startsWith(VERSIONS) || !rel.endsWith('.py')) return null;
  return rel;
}

/** True when the path exists on origin/main (or on main when there is no origin). */
function merged(rel) {
  for (const ref of ['origin/main', 'main']) {
    const known = spawnSync('git', ['rev-parse', '--verify', '--quiet', `${ref}^{commit}`], {
      cwd: projectDir,
      encoding: 'utf8',
    });
    if (known.error || known.status !== 0) continue;
    const found = spawnSync('git', ['cat-file', '-e', `${ref}:${rel}`], { cwd: projectDir });
    return found.status === 0;
  }
  return false;
}

/** Text the call adds: the Write content or every Edit/MultiEdit new_string. */
function addedText(toolInput) {
  if (typeof toolInput.content === 'string') return toolInput.content;
  const parts = [];
  if (typeof toolInput.new_string === 'string') parts.push(toolInput.new_string);
  for (const edit of toolInput.edits || []) {
    if (typeof edit.new_string === 'string') parts.push(edit.new_string);
  }
  return parts.join('\n');
}

/** The file as it would be after the call. */
function resultingText(file, toolInput) {
  if (typeof toolInput.content === 'string') return toolInput.content;
  let text = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
  const edits = toolInput.edits || [toolInput];
  for (const edit of edits) {
    if (typeof edit.old_string !== 'string' || typeof edit.new_string !== 'string') continue;
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
    if (inHelper && line.trim() !== '' && !/^\s/.test(line)) inHelper = false;
    if (!inHelper) kept.push(line);
  }
  return /enable_(tenant|shared)_rls\s*\(|ENABLE ROW LEVEL SECURITY/.test(kept.join('\n'));
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  input += chunk;
});
process.stdin.on('end', () => {
  let payload;
  try {
    payload = JSON.parse(input);
  } catch {
    return;
  }
  const toolInput = payload.tool_input || {};
  const rel = migrationPath(toolInput.file_path);
  if (rel === null) return;
  if (merged(rel)) {
    process.stderr.write(
      `Заблокировано: ${rel} уже в main, а влитые миграции не редактируют ` +
        '(CLAUDE.md → «Как работать»). Добавьте новую миграцию со следующим номером.\n',
    );
    process.exit(2);
  }
  const file = path.join(projectDir, rel);
  if (/op\.create_table\s*\(/.test(addedText(toolInput)) && !turnsOnRls(resultingText(file, toolInput))) {
    process.stderr.write(
      `Заблокировано: ${rel} создаёт таблицу, но не включает Row-Level Security ` +
        '(CLAUDE.md → «Мультиарендность»). Таблице визовика нужны tenant_id и ' +
        'enable_tenant_rls(<таблица>) — образец в миграции 0001; общей справочной — ' +
        'enable_shared_rls (миграция 0009). Добавьте вызов в ту же правку.\n',
    );
    process.exit(2);
  }
});
