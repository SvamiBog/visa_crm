# Third-party notice: ECC

Some files in `.claude/` are adapted from **ECC** (github.com/affaan-m/ECC), tag `v2.2.3`, commit `c05b2d6614f62f6db0047669aa4eefb223d478f9`, under the MIT License below. Each adapted file names its source in its first line after the front matter. They were rewritten for VisaCRM: project commands, paths and rules instead of generic examples; references to ECC components not copied here removed; no checkpoint commits, no package downloads, nothing published to GitHub without the user's request. `CLAUDE.md` and the project docs take precedence over them.

The ECC plugin itself is not installed: cloud sessions load no plugins, so local and cloud sessions use these same files.

## Adapted files

| File in this repository | ECC source |
|---|---|
| `.claude/rules/ecc/common/coding-style.md` | `rules/common/coding-style.md` |
| `.claude/rules/ecc/common/testing.md` | `rules/common/testing.md` |
| `.claude/rules/ecc/common/security.md` | `rules/common/security.md` |
| `.claude/rules/ecc/common/git-workflow.md` | `rules/common/git-workflow.md` |
| `.claude/rules/ecc/common/development-workflow.md` | `rules/common/development-workflow.md` |
| `.claude/rules/ecc/common/code-review.md` | `rules/common/code-review.md` |
| `.claude/rules/ecc/python/coding-style.md` | `rules/python/coding-style.md`, `patterns.md`, `security.md` |
| `.claude/rules/ecc/python/fastapi.md` | `rules/python/fastapi.md` |
| `.claude/rules/ecc/python/testing.md` | `rules/python/testing.md` |
| `.claude/rules/ecc/typescript/coding-style.md` | `rules/typescript/coding-style.md`, `patterns.md`, `security.md` |
| `.claude/rules/ecc/typescript/testing.md` | `rules/typescript/testing.md` |
| `.claude/commands/ecc-plan.md` | `commands/plan.md` |
| `.claude/commands/ecc-code-review.md` | `commands/code-review.md` (its PR mode credits PRPs-agentic-eng by Wirasm) |
| `.claude/commands/ecc-save-session.md` | `commands/save-session.md` |
| `.claude/commands/ecc-resume-session.md` | `commands/resume-session.md` |
| `.claude/commands/ecc-python-review.md` | `commands/python-review.md` |
| `.claude/commands/ecc-fastapi-review.md` | `commands/fastapi-review.md` |
| `.claude/commands/ecc-react-review.md` | `commands/react-review.md` |
| `.claude/agents/ecc-python-reviewer.md` | `agents/python-reviewer.md` |
| `.claude/agents/ecc-fastapi-reviewer.md` | `agents/fastapi-reviewer.md` |
| `.claude/agents/ecc-database-reviewer.md` | `agents/database-reviewer.md` (credits Supabase Agent Skills, MIT) |
| `.claude/agents/ecc-security-reviewer.md` | `agents/security-reviewer.md` |
| `.claude/agents/ecc-typescript-reviewer.md` | `agents/typescript-reviewer.md` |
| `.claude/agents/ecc-react-reviewer.md` | `agents/react-reviewer.md` |
| `.claude/skills/ecc-tdd-workflow/SKILL.md` | `skills/tdd-workflow/SKILL.md` |
| `.claude/skills/ecc-database-migrations/SKILL.md` | `skills/database-migrations/SKILL.md` |
| `.claude/skills/ecc-e2e-testing/SKILL.md` | `skills/e2e-testing/SKILL.md` |
| `.claude/skills/ecc-i18n-sync/SKILL.md` | `skills/i18n-sync/SKILL.md` |

The hooks in `.claude/hooks/` and `.claude/settings.json` are this project's own and do not come from ECC.

## Updating

To take a newer ECC release: read its changes to the files above as data, carry over what fits the project by hand, update the tag and commit here, and check the rules again (each path and symbol they name must exist).

## License

```text
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
```
