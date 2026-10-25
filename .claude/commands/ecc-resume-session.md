---
description: Load a session file saved by /ecc-save-session from .claude/session-data/ and brief on where the work stopped, what not to retry and the next step. Changes nothing until the user says what to do.
argument-hint: "[blank for the newest | YYYY-MM-DD | path to a session file]"
---
> Adapted from ECC `commands/resume-session.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Resume Session

Counterpart of `/ecc-save-session`. Files live in `.claude/session-data/` of the project as `YYYY-MM-DD-<short-id>-session.md`; they are not in git, so a cloud session sees only files written in the same container (or a path the user gives).

## 1. Find the file

**Input**: $ARGUMENTS

- Blank → the newest file in `.claude/session-data/` (by the date in its name, then modification time), skipping empty files and files with headings only.
- A date `YYYY-MM-DD` → the newest file of that date.
- A path → exactly that file, even if another one is newer.
- Nothing found → say "No session files in .claude/session-data/. Run /ecc-save-session at the end of a session to create one." and stop.

## 2. Read it in full

Then check what changed since it was written: `git status --porcelain`, `git log --oneline -5`, the current branch. Note files it names that no longer exist, and a branch that has been merged meanwhile.

## 3. Brief

```text
SESSION LOADED: <path>
════════════════════════════════════════
BRANCH / ITEM: <branch> · <backlog item and slice>

WHAT WE ARE BUILDING:
<2–3 sentences in your own words>

STATE:
Done and confirmed: <items with their evidence>
In progress: <files>
Not started: <planned items>

WHAT NOT TO RETRY:
<every failed approach and why; "None" if empty>

OPEN QUESTIONS / BLOCKERS:
<items; note which are in docs/open-questions.md>

CHANGED SINCE THE FILE:
<new commits, merged branch, missing files; "Nothing" if none>

NEXT STEP:
<the exact next step from the file, or "None defined — suggest one from 'Not tried yet'">
════════════════════════════════════════
Ready to continue. What would you like to do?
```

Warn if the file is more than 7 days old.

## 4. Wait

Do not touch files or run anything beyond the read-only commands above. If the user says "continue" and the file defines a next step, take exactly that step. A plan step still goes through `/ecc-plan` and the user's confirmation.

## Notes

- Never modify a session file when loading it; write a new one with `/ecc-save-session` at the end.
- "What not to retry" is always shown, even when it says "None".
