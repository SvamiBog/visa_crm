---
name: ecc-context-budget
description: Audits Claude Code context consumption in VisaCRM across CLAUDE.md, .claude/rules, agents, skills, commands and MCP servers. Identifies bloat and overlap and produces prioritized token-savings recommendations. Use when the context fills too fast or before adding components.
metadata:
  origin: ECC
---

> Adapted from ECC `skills/context-budget/SKILL.md` v2.2.3 (github.com/affaan-m/ECC, MIT; see `.claude/ECC-NOTICE.md`). `CLAUDE.md` and `.claude/rules/` take precedence over this file.
>
> VisaCRM: components live in the repository's `.claude/` (the `ecc-` ones come from ECC, `.claude/ECC-NOTICE.md`). The session-file byte counter of the original is removed: it reads files outside the project. Report only; change nothing without the user's agreement.

# Context Budget

Analyze token overhead across every loaded component in a Claude Code session and surface actionable optimizations to reclaim context space.

## When to Use

- Session output quality degrades or the context fills quickly
- After adding agents, skills, rules or MCP servers
- Before adding more components, to know whether there is room

## How It Works

### Phase 1: Inventory

Estimate token consumption (prose: words × 1.3; code-heavy files: chars / 4; Russian text costs more — count it as chars / 3):

**CLAUDE.md** — the project's `CLAUDE.md` (always loaded)
- Flag sections that repeat what `.claude/rules/` or `docs/` already say

**Rules** (`.claude/rules/**/*.md`)
- Files without `paths:` frontmatter (`ecc/common/`) load in every session: their total should stay within ~3–4K tokens
- Files with `paths:` (`ecc/python/`, `ecc/typescript/`) load only when matching files are read
- Detect overlap between rules and with `CLAUDE.md`

**Agents** (`.claude/agents/*.md`)
- The `description` of every agent is always in context; flag descriptions over ~30 words
- Flag files over ~200 lines (they load on every spawn)

**Skills** (`.claude/skills/*/SKILL.md`) and **commands** (`.claude/commands/*.md`)
- Descriptions are always in context, bodies only on use; flag bodies over ~400 lines

**MCP servers** — the repository has no `.mcp.json`; user-level servers are the user's
- Estimate ~500 tokens per tool schema; flag servers with many tools that duplicate a CLI already available (`gh`, `git`)

For the harness's own numbers use `/context` in the session.

### Phase 2: Classify

| Bucket | Criteria | Action |
|--------|----------|--------|
| **Always needed** | Referenced in CLAUDE.md or the rules, backs a command in use | Keep |
| **Sometimes needed** | Area-specific (Python, frontend) | Path-scoped rule or skill |
| **Rarely needed** | No reference, overlapping content | Propose removal |

### Phase 3: Detect Issues

- **Bloated descriptions** — loaded into every session
- **Heavy agents** — inflate every spawn
- **Redundancy** — a rule repeating `CLAUDE.md`, a skill repeating an agent
- **Unscoped language rules** — Python or frontend rules without `paths:`
- **CLAUDE.md bloat** — details that belong in `docs/` or a path-scoped rule

### Phase 4: Report

```
Context Budget Report
═══════════════════════════════════════

Always loaded: ~X,XXX tokens (CLAUDE.md ~X,XXX, rules ~X,XXX, descriptions ~X,XXX)
Path-scoped rules: python ~X,XXX, typescript ~X,XXX

Issues Found (N), ranked by savings:
1. [action] → save ~X,XXX tokens
2. [action] → save ~X,XXX tokens
3. [action] → save ~X,XXX tokens
```

## Best Practices

- **Descriptions are always loaded**: keep them to one or two sentences
- **Scope by path**: language rules only load with their files
- **Audit after changes**: run after adding any agent, skill, rule or MCP server
- **Changing `CLAUDE.md` is a project change**: propose it, do not apply it silently
