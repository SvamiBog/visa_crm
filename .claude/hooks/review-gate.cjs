#!/usr/bin/env node
// The review gate of docs/process.md, as a PreToolUse hook and a small command.
//
// Before a branch leaves the machine (git push, gh pr create, a GitHub pull-request
// tool), every area the branch changes needs a fresh "pass" from its reviewer, the way
// a team signs off a change before it ships:
//
//   gate      files the branch changes                         reviewer
//   code      backend/, frontend/ (not locales), ops/, Dockerfile  agent ecc-code-reviewer
//   db        backend/app/models/, backend/migrations/          agent ecc-database-reviewer
//   design    frontend/src/**/*.tsx, *.css, frontend/index.html  agent design-qa
//   copy      frontend/src/locales/, backend/app/core/notices/  agent copy-editor
//   security  login, sessions, access, vault, files, uploads, portal, signing, PDFs,
//             the Telegram bot (SECURITY and SECURITY_BY_NAME below)
//                                                               agent ecc-security-reviewer
//   legal     what clients are told and agree to (LEGAL below)  agent legal-advisor
//
// A reviewer records its verdict when it has reviewed the branch:
//   node .claude/hooks/review-gate.cjs record <gate> pass|fail [--note "…"]
// The record keeps the content (git blob ids) of the gate's changed files as the reviewer
// saw them, committed or not. A file changed after the review makes it stale, and the
// gate asks for that review again. `status` prints what the branch needs and has.
//
// The person may let one push through without reviews (a hotfix, a docs fix the gate
// misreads): only on their words in the chat, by putting REVIEW_GATE=skip before the
// command. If git cannot answer, the hook lets the call through with a warning rather
// than stopping work. Exit 2 blocks the call and shows stderr to Claude.
//
// git is GIT_EXE if set, else from PATH.
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const SECURITY = new RegExp(
  "^backend/app/(main\\.py|api/(auth|deps|two_factor|email_change|vault|documents|package_files|" +
    "portal|portal_public|imports|employees|case_access|admin|workspace)\\.py|core/(security|crypto|" +
    "vault|documents|storage|two_factor|passwords|employee_limits|request_limit|http_headers|" +
    "import_uploads|data_import|workbook|portal|db|config|error_reports|erase|data_export|" +
    "passport_purge|modules)\\.py)$",
);
// What the client reaches from outside or signs: the portal, signing and PDFs, the Telegram
// bot and its webhooks, client links and consent. Matched by name so that a module added
// later (core/signing.py, api/telegram_webhook.py, a bot package) is covered from its first PR.
const SECURITY_BY_NAME = new RegExp(
  "^backend/app/((api|core)/[^/]*(sign|pdf|gotenberg|telegram|bot|portal|webhook|client_link|consent)[^/]*\\.py|" +
    "(bot|telegram|signing|esign)/.*)$",
);

// The privacy notices clients read and the code that shows them and records their
// consent: a change here is a legal question (docs/process.md → «Юрист»).
const LEGAL = /^backend\/app\/core\/(notices\/|portal_notice\.py$)/;

const GATES = {
  code: {
    reviewer: "ecc-code-reviewer",
    what: "код",
    test: (p) =>
      (/^(backend|frontend|ops)\//.test(p) || p === "Dockerfile") && !p.startsWith("frontend/src/locales/"),
  },
  db: {
    reviewer: "ecc-database-reviewer",
    what: "модели и миграции",
    test: (p) => /^backend\/(app\/models|migrations)\//.test(p),
  },
  design: {
    reviewer: "design-qa",
    what: "экраны",
    test: (p) => /^frontend\/src\/.*\.(tsx|css)$/.test(p) || p === "frontend/index.html",
  },
  copy: {
    reviewer: "copy-editor",
    what: "тексты интерфейса",
    test: (p) => /^frontend\/src\/locales\/.*\.json$/.test(p) || p.startsWith("backend/app/core/notices/"),
  },
  security: {
    reviewer: "ecc-security-reviewer",
    what: "вход, доступ, сейф, файлы, загрузки",
    test: (p) => SECURITY.test(p) || SECURITY_BY_NAME.test(p),
  },
  legal: {
    reviewer: "legal-advisor",
    what: "уведомления и согласия клиентов",
    test: (p) => LEGAL.test(p),
  },
};

function git(dir, args) {
  const out = spawnSync(process.env.GIT_EXE || "git", ["-C", dir, ...args], {
    encoding: "utf8",
    timeout: 15000,
    maxBuffer: 32 * 1024 * 1024,
  });
  if (out.error) throw new Error(`git unavailable (${out.error.message})`);
  return out;
}

function gitOk(dir, args) {
  const out = git(dir, args);
  if (out.status !== 0) throw new Error(`git ${args[0]}: ${(out.stderr || "").trim()}`);
  return out.stdout;
}

const lines = (text) => text.split(/\r?\n/).filter(Boolean);

/** The repository's root, the branch and the commit the branch started from. */
function context(dir) {
  const root = gitOk(dir, ["rev-parse", "--show-toplevel"]).trim();
  const branch = gitOk(root, ["rev-parse", "--abbrev-ref", "HEAD"]).trim();
  let base = null;
  for (const ref of ["origin/main", "main"]) {
    const found = git(root, ["merge-base", "HEAD", ref]);
    if (found.status === 0) {
      base = found.stdout.trim();
      break;
    }
  }
  if (!base) throw new Error("neither origin/main nor main found");
  return { root, branch, base };
}

function storeFile(ctx) {
  const name = ctx.branch.replace(/[^A-Za-z0-9._-]+/g, "__");
  return path.join(ctx.root, ".claude", "session-data", "review-gates", `${name}.json`);
}

function readStore(ctx) {
  try {
    return JSON.parse(fs.readFileSync(storeFile(ctx), "utf8"));
  } catch {
    return { branch: ctx.branch, gates: {} };
  }
}

/** Changed files and their content as committed: what a push sends. */
function committed(ctx) {
  const files = {};
  for (const p of lines(gitOk(ctx.root, ["diff", "--name-only", "--no-renames", ctx.base, "HEAD"]))) {
    const blob = git(ctx.root, ["rev-parse", "--verify", "--quiet", `HEAD:${p}`]);
    files[p] = blob.status === 0 ? blob.stdout.trim() : "deleted";
  }
  return files;
}

/** Changed files and their content in the working tree: what a reviewer sees. */
function working(ctx) {
  const names = new Set([
    ...lines(gitOk(ctx.root, ["diff", "--name-only", "--no-renames", ctx.base])),
    ...lines(gitOk(ctx.root, ["ls-files", "--others", "--exclude-standard"])),
  ]);
  const files = {};
  for (const p of names) {
    const full = path.join(ctx.root, p);
    // hash-object with the path applies the repository's filters (line endings), so the
    // id equals the blob the file becomes when committed.
    files[p] = fs.existsSync(full) ? gitOk(ctx.root, ["hash-object", "--", p]).trim() : "deleted";
  }
  return files;
}

function only(files, gate) {
  return Object.fromEntries(Object.entries(files).filter(([p]) => GATES[gate].test(p)));
}

/** For each gate the change needs (by default the committed one, what a push sends): ok, missing, failed or stale. */
function evaluate(ctx, files = committed(ctx)) {
  const store = readStore(ctx);
  const result = [];
  for (const gate of Object.keys(GATES)) {
    const needed = only(files, gate);
    if (!Object.keys(needed).length) continue;
    const record = store.gates[gate];
    if (!record) {
      result.push({ gate, state: "missing" });
      continue;
    }
    const changed = Object.entries(needed)
      .filter(([p, id]) => record.files[p] !== id)
      .map(([p]) => p);
    if (changed.length) result.push({ gate, state: "stale", changed, record });
    else if (record.verdict !== "pass") result.push({ gate, state: "failed", record });
    else result.push({ gate, state: "ok", record });
  }
  return result;
}

function describe(item) {
  const g = GATES[item.gate];
  const head = `- ${item.gate} (${g.what}) — агент ${g.reviewer}`;
  if (item.state === "ok") return `${head}: принято ${item.record.at}`;
  if (item.state === "missing") return `${head}: проверки не было`;
  if (item.state === "failed") return `${head}: на доработку (${item.record.at}${item.record.note ? ", " + item.record.note : ""})`;
  const shown = item.changed.slice(0, 8).join(", ") + (item.changed.length > 8 ? ", …" : "");
  return `${head}: устарела, после проверки изменились ${shown}`;
}

function record(ctx, gate, verdict, note) {
  if (!GATES[gate]) throw new Error(`unknown gate "${gate}": ${Object.keys(GATES).join(", ")}`);
  if (verdict !== "pass" && verdict !== "fail") throw new Error('verdict is "pass" or "fail"');
  const store = readStore(ctx);
  const files = only(working(ctx), gate);
  store.gates[gate] = { verdict, at: new Date().toISOString(), note: note || "", files };
  fs.mkdirSync(path.dirname(storeFile(ctx)), { recursive: true });
  fs.writeFileSync(storeFile(ctx), JSON.stringify(store, null, 2) + "\n");
  const count = Object.keys(files).length;
  console.log(`review gate ${gate}: ${verdict} recorded for ${ctx.branch} (${count} file(s) of the gate)`);
}

/** What the branch needs now: the working tree (committed or not), as a reviewer sees it. */
function status(ctx) {
  const result = evaluate(ctx, working(ctx));
  console.log(`Ветка ${ctx.branch}, от ${ctx.base.slice(0, 7)}; по рабочей копии, с незакоммиченным.`);
  if (!result.length) {
    console.log("Изменения не задевают ни одной области с обязательной проверкой.");
    return;
  }
  for (const item of result) console.log(describe(item));
  const left = result.filter((r) => r.state !== "ok").length;
  console.log(left ? `Осталось проверок: ${left}.` : "Все проверки пройдены.");
}

/** The hook: blocks a push or a pull request while a needed review is missing or stale. */
function hook() {
  let input;
  try {
    input = JSON.parse(fs.readFileSync(0, "utf8"));
  } catch {
    return 0; // not a hook payload
  }
  const tool = input.tool_name || "";
  const command = (input.tool_input && input.tool_input.command) || "";
  let ships = false;
  if (tool === "Bash" || tool === "PowerShell") {
    // Words inside a commit message, a heredoc or a here-string are text, not commands.
    const bare = command
      .replace(/<<-?\s*(['"]?)(\w+)\1[^\n]*\n[\s\S]*?\n\s*\2[ \t]*(?=\n|$)/g, "\n")
      .replace(/@'[\s\S]*?'@|@"[\s\S]*?"@/g, "''")
      .replace(/'[^']*'|"(?:[^"\\]|\\.)*"/g, "''");
    const push = /(^|[\s;&|(])git(\s+-C\s+\S+)?\s+push\b/.test(bare) && !/\s--delete\b|\s-d\s/.test(bare);
    const pr = /(^|[\s;&|(])gh\s+pr\s+create\b/.test(bare);
    ships = push || pr;
    if (ships && /\bREVIEW_GATE=skip\b/.test(command)) {
      process.stderr.write("[review-gate] skipped by REVIEW_GATE=skip (only on the person's words)\n");
      return 0;
    }
  } else if (/github/i.test(tool) && /create_pull_request|create_pr\b/i.test(tool)) {
    ships = true;
  }
  if (!ships) return 0;

  const ctx = context(input.cwd || process.cwd());
  const result = evaluate(ctx);
  const blocked = result.filter((r) => r.state !== "ok");
  if (!blocked.length) return 0;
  process.stderr.write(
    `Ветка ${ctx.branch} не прошла обязательные проверки (docs/process.md → «Проверки перед PR»):\n` +
      blocked.map(describe).join("\n") +
      "\n\nЗапустите названных агентов на изменения ветки (по 2–3 одновременно); каждый в конце " +
      "сам записывает вердикт командой `node .claude/hooks/review-gate.cjs record <область> pass|fail`. " +
      "Исправьте находки и повторите проверку тех областей, где после неё менялись файлы. " +
      "Состояние — `node .claude/hooks/review-gate.cjs status`. Пропустить проверку можно только " +
      "по прямым словам человека в чате: REVIEW_GATE=skip перед командой.\n",
  );
  return 2;
}

function main() {
  const [command, ...rest] = process.argv.slice(2);
  if (command === "record") {
    const noteAt = rest.indexOf("--note");
    const note = noteAt >= 0 ? rest[noteAt + 1] : "";
    record(context(process.cwd()), rest[0], rest[1], note);
    return 0;
  }
  if (command === "status") {
    status(context(process.cwd()));
    return 0;
  }
  return hook();
}

let code = 0;
try {
  code = main();
} catch (error) {
  const cli = ["record", "status"].includes(process.argv[2]);
  process.stderr.write(`[review-gate] ${error.message}${cli ? "" : "; not checked"}\n`);
  code = cli ? 1 : 0;
}
process.exit(code);
