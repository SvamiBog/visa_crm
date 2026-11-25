---
description: Close a discussion and hand it to development — the decisions are checked by the product-analyst agent and cut into backlog items ready to build, written into the docs and the roadmap page after the owner's yes, and a development session is proposed for each item that can start now.
argument-hint: "[topic, optional]"
---
# Handoff

The bridge from a discussion session to development (`docs/process.md`). Run it in the session where the idea was discussed, when the owner says it is decided.

**Input**: $ARGUMENTS — the topic, if the conversation had several.

## 1. Sum up the discussion

In a few lines, from this conversation: what the specialist (employee, admin) should be able to do; the decisions taken and why; the options rejected; what is still open. Show it to the owner and fix it if they correct you.

## 2. Product analysis — agent `product-analyst`

Give it the summary (it does not see this conversation). It returns the items in the backlog's style with acceptance criteria, areas and reviewers, the level of each (обычный / сложный / самый сложный), conflicts with decisions, open questions, what already exists, and the lanes of parallel work. If the analyst finds conflicts with decisions or the idea is architectural, run it with `model: "fable"` and `effort: "xhigh"` (`docs/process.md` → «Модель и уровень размышления»).

Legal questions it raises go to the agent `legal-advisor` (Fable 5.2, maximum effort) before the owner is asked: the owner gets its conclusion, risks and options, not a bare legal question.

## 3. Owner's yes

Show the items, the questions and the lanes. Ask the questions as the analyst worded them. WAIT for the owner's yes or changes; change the items accordingly. An item whose question is still open is not ready: it goes in as a proposal, not as a task.

## 4. Write it down

In a worktree of this session from `origin/main` (branch `claude/plan-<topic>`):

- `docs/backlog.md` — the ready items under their stage, in order;
- `docs/roadmap.md` — the stage's epics or summary line if they change;
- `docs/decisions.md` — each decision taken in the discussion, with the date and «решение владельца»;
- `docs/open-questions.md` — each question left open;
- the roadmap page (ArtifactData, collection `items`; read before writing): a ready item gets status `todo` («Не начато») with the note «Готово к разработке, <дата>»; an item waiting for an answer — `waiting`; an idea not yet cut into items — `idea` («Предложение»).

Commit `Docs: plan <topic> (backlog X.Y–X.Z)`, push and open the PR on the owner's yes. It changes only docs, so the review gate asks for nothing. Merge it when CI is green, on the owner's word: development sessions start from `origin/main` and must see the items.

## 5. Development sessions

For the first lane — items that can start now without colliding (2–3 at most; one item with a migration at a time; items on the same screen or locale section one after another): propose a session per item, `context: "fresh"`, own worktree, and file it under the sidebar section «Разработка» if there is one. If the owner asked to start them, start them; otherwise propose them as cards. Each brief:

```text
/deliver X.Y
<the item's text from docs/backlog.md, in full>
Lane: runs in parallel with X.Z (screens of <…>); do not touch <files of the other item>.
Mockup: needed / not needed — <why>.
Level: обычный / сложный / самый сложный — <why>.
<anything the owner said about this item in the discussion, quoted>
```

For a сложный item, raise the new session to Fable 5.2 and `xhigh` right after starting it (`set_session_model`, `set_session_effort`; `max` for самый сложный); the app asks the owner to approve the more expensive model.

The next lanes start as the merges they wait for land: say which, and propose them then.
