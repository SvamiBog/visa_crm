# ECC components in this repository

Some Claude Code files under `.claude/` are adapted from ECC (github.com/affaan-m/ECC), version 2.2.3, commit `c05b2d6614f62f6db0047669aa4eefb223d478f9`, under the MIT License below. Each file names its source in the first lines. `CLAUDE.md` and `.claude/rules/` take precedence over them.

They are copied into the repository rather than installed as the ECC plugin because cloud sessions of Claude Code load only the repository's files, never plugins.

| File | Source in ECC | Changes |
|------|---------------|---------|
| `.claude/agents/ecc-planner.md` | `agents/planner.md` | `ecc-` prefix, project note, worked example and red flags for VisaCRM |
| `.claude/agents/ecc-tdd-guide.md` | `agents/tdd-guide.md` | rewritten for pytest on PostgreSQL, fakes instead of mocks, no coverage threshold |
| `.claude/agents/ecc-code-reviewer.md` | `agents/code-reviewer.md` | prefix, Python and project examples, tenant isolation, money, audit, i18n checks; Next.js and cost sections removed |
| `.claude/agents/ecc-fastapi-reviewer.md` | `agents/fastapi-reviewer.md` | prefix, project layout, dependencies, error codes and audit |
| `.claude/agents/ecc-database-reviewer.md` | `agents/database-reviewer.md` | rewritten for Alembic and the project's RLS model; Supabase patterns removed |
| `.claude/agents/ecc-react-reviewer.md` | `agents/react-reviewer.md` | prefix, React 18 SPA (server components and Next.js removed), TanStack Query, i18n and theme, type safety; eslint commands removed |
| `.claude/agents/ecc-security-reviewer.md` | `agents/security-reviewer.md` | prefix, project risk areas and CI's audit commands; Node/Express tooling removed |
| `.claude/skills/ecc-tdd-workflow/SKILL.md` | `skills/tdd-workflow/SKILL.md` | rewritten for pytest; no package-manager detector, checkpoint commits or evidence files |
| `.claude/skills/ecc-i18n-sync/SKILL.md` | `skills/i18n-sync/SKILL.md` | rewritten for `ru.json` / `en.json`, i18next plurals, error and audit keys; Locakit removed |
| `.claude/skills/ecc-context-budget/SKILL.md` | `skills/context-budget/SKILL.md` | prefix, project paths, path-scoped rules; session-file byte counter removed |
| `.claude/commands/ecc-plan.md` | `commands/plan.md` | prefix, backlog items as input, plans in the conversation; PRD artifacts and links to absent commands removed |
| `.claude/commands/ecc-code-review.md` | `commands/code-review.md` | prefix, project checklist and checks, report in the conversation, publishing only when asked, REST fallback for cloud sessions |
| `.claude/commands/ecc-fastapi-review.md` | `commands/fastapi-review.md` | prefix, project review areas and checks |
| `.claude/commands/ecc-react-review.md` | `commands/react-review.md` | prefix, `npm run build` instead of eslint, project categories |
| `.claude/rules/*.md`, `.claude/rules/python/*.md`, `.claude/rules/typescript/*.md` | `rules/common/`, `rules/python/`, `rules/typescript/`, `rules/react/` | rewritten for VisaCRM |

ECC's hooks and scripts are not included; the project's own hooks are in `.claude/hooks/`. When updating to a newer ECC version, review its changes first (`git diff c05b2d6..<tag> -- agents skills commands rules`) and re-apply these adaptations.

## License

MIT License

Copyright (c) 2026 Affaan Mustafa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
