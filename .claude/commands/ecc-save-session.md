---
description: Save the state of this session (what was built, what worked with evidence, what failed and why, what is next) to a dated file in .claude/session-data/ so a later session can resume it.
---
> Adapted from ECC `commands/save-session.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Save Session

## Steps

1. **Gather**: `git status --porcelain` and `git diff HEAD` for the files touched; what was discussed, tried and decided; errors met and how they ended; the last test and lint results.
2. **Write** `.claude/session-data/YYYY-MM-DD-<short-id>-session.md` in the project (kept out of git by `.gitignore`; a cloud session loses it with its container): today's date, a lowercase short id of 8+ letters, digits or hyphens. Each session gets its own file — never append to an earlier one.
3. **No personal data**: no client names, phones, passport numbers or vault contents — ids and technical facts only.
4. Fill every section; write "None" where a section is genuinely empty.
5. **Show** the file and ask: "Session saved to <path>. Anything to correct or add?" Edit if asked.

## Format

```markdown
# Session: YYYY-MM-DD

**Branch:** <branch> · **Backlog item:** <item and slice>
**Topic:** <one line>

## What we are building
<1–3 paragraphs: the goal, why, how it fits; enough for someone with no memory of this session>

## What worked (with evidence)
- **<thing>** — confirmed by: <test run, command output, check>

## What did not work (and why)
- **<approach>** — failed because: <exact reason or error>

## Not tried yet
- <approach or idea>

## State of files
| File | Status | Notes |
| --- | --- | --- |

## Decisions made
- **<decision>** — reason: <why>; recorded in `docs/decisions.md`: yes / no

## Blockers and open questions
- <item>; in `docs/open-questions.md`: yes / no

## Exact next step
<the single next action, precise enough to start without thinking>

## Environment notes
<services and commands needed, only if not the standard setup in `CLAUDE.md`>
```

## Notes

- "What did not work" matters most: without it the next session retries failed approaches.
- Saving mid-session is fine: mark unfinished items clearly.
