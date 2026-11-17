---
name: buttons-and-spacing
description: "Check and fix how the buttons and spacing of a VisaCRM screen look, on a phone first: three kinds of buttons coloured from the theme (main, ordinary, dangerous), a gap in every row of buttons, blocks and cards on one spacing scale, text and links lined up. Looks at the running app at phone and desktop widths in all four themes with screenshots, fixes styles.css (and a button's class where its kind is wrong), proves it with frontend/e2e/buttons.spec.ts and reports before/after. Use when buttons look stuck together, unclear, colourless or all alike, or the spacing of a screen is uneven or doubled."
---
# Buttons and Spacing

The project's own skill (not from ECC). `CLAUDE.md` and `docs/decisions.md` («Отделка интерфейса», «Кнопки и отступы») take precedence over this file.

A visa specialist works from a phone as much as from a desktop. A button there must look like a button before it is touched (a phone has no hover), and two buttons must never touch: on a phone a stuck pair is tapped by mistake. This skill keeps every screen to the rules below.

## The rules

### Three kinds of buttons

| Kind | Class | For | Look |
| --- | --- | --- | --- |
| Main | `btn btn-p` | the one action the block or open form is for: «+ Платёж», «Сохранить» | filled with the accent (`--c-primary`), text `--c-on-primary`, weight 600 |
| Ordinary | `btn` | every other action: «Изменить», «+ Бронь», «Отмена» | a fill of its own (`--c-btn`) and an edge (`--c-btn-border`), off both the card and the page in every theme; weight 500 |
| Dangerous | `btn btn-danger` | deletes or switches off: «Удалить карточку», «Отключить» | red text `--c-red` and a reddish edge before it is touched; red fill (`--c-red-bg`) when pressed |

- One main button per screen or open form (decisions «Отделка интерфейса»); on the client's card it is «+ Платёж». More main buttons is not "more colour", it is no main action.
- A small action inside a row or a line of text («Изменить» of a payment, «Показать все поля») is a `link-button`, not a `btn`.
- `btn-sm` (36px) in a card's head and in a list; the full `btn` (44px) for a page's header and a form's own buttons.
- States: hover (mouse only, under `@media (hover: hover)`) and pressed (`:active`, also a phone) tint an ordinary button with `--c-primary-soft`; disabled is `opacity: 0.55`. A tapped button must not stay lit, so no hover rules outside that media query.

### Colours come from the theme

Buttons use only the tokens of `frontend/src/styles.css`: `--c-btn`, `--c-btn-border`, `--c-primary*`, `--c-on-primary`, `--c-red`, `--c-red-bg`. Each of the four themes (`blue`, `dark`, `warm`, `minimal`) defines `--c-btn` and `--c-btn-border` itself; a new theme defines them too. A button whose fill equals the card or the page under it is the bug this skill exists for (the dark theme had black boxes).

### Spacing

Multiples of 4px (decisions «Отделка интерфейса»): 10px only between an icon and its label, small adjustments inside buttons, fields and chips allowed.

| Space | Where |
| --- | --- |
| 4 | label to its field, a chip's inside, a muted line under a title |
| 8 | between buttons in a row; a row of a list (`.kv`, `.plain-list li` padding) |
| 12 | a card's head to its content (`.card-head`); items of a list inside a block |
| 16 | parts inside a card, fields of a form (`.form-grid`), a phone's side gutter (`.content`) |
| 20 | between cards and sections of a page (`.page`, `.column`, `.card-grid`), a card's padding |

- **A row of buttons is a container with a gap:** `.actions`, `.form-actions`, `.card-head-actions`, `.head-actions` — flex, `gap: 8px`, `flex-wrap: wrap`, so on a phone they wrap instead of running off the screen. Never put buttons side by side in an element without a gap, and never space buttons with margins.
- **One source of space between parts:** inside a flex or grid column the `gap` is the space; its children (`p`, `.card-head`, lists) get `margin: 0`, or the space doubles. A part that can be empty (a list with no items yet) is hidden with `:empty` so it adds no gap.
- **One edge inside a card:** headings, labels, lines of text, a link on its own line and the first column of a table start at the card's padding. A `link-button` alone on its line has `padding-inline: 0` (`.card > .link-button`); a table inside a card has no outer cell padding (`.money-table`).
- On a phone (under 720px) the page is one column and the tab bar covers the bottom: the last block needs the page's bottom padding (`.content`), not its own.

## Workflow

### 1. Stand

The built frontend served by the API with demo data, as in the `ecc-e2e-testing` skill (`npm run build`, `alembic upgrade head`, `seed-demo`, uvicorn on :8000). A cloud session has no `node_modules`: `npm ci` in `frontend/` first. Chromium is already installed; do not run `playwright install`.

### 2. Screenshots before

```bash
DEMO_PASSWORD=… node .claude/skills/buttons-and-spacing/screens.cjs \
  --out <scratchpad>/before --path "/app/clients/{case-client}"
```

The whole page, its header and each card on its own, at 390px and 1280px in all four themes (`--widths`, `--themes`, `--lang` to narrow; `{plain-client}` for a card that shows «Удалить карточку»). Look at the 390px dark and blue ones first — the phone is where the trouble shows — then the rest. The person does not see these files: the before/after pairs that matter go into the report.

### 3. Checklist for each screenshot

- [ ] Every row of buttons has a gap; nothing touches or overlaps; a row wraps cleanly at 390px.
- [ ] Each button reads as a button in every theme: its own fill, not the card's or the page's.
- [ ] Kinds are right: one main button per screen or form, dangerous ones red before a touch, small in-row actions as links.
- [ ] Spaces are from the scale and not doubled (a heading with a large hole under it is usually a margin plus a gap).
- [ ] Texts, links and tables in a card start on one edge.
- [ ] Nothing runs off the right edge at 390px; long labels wrap inside their block.

Write down each finding with the screenshot, the element and the CSS rule that causes it (`grep -n` in `styles.css`), before changing anything.

### 4. Test first

`frontend/e2e/buttons.spec.ts` checks the client's card in both languages, at 390px and 1280px, in all four themes switched in place: buttons side by side keep 8px, no button has its backdrop's fill, «Удалить карточку» is red untouched. A screen you fix gets the same checks: add it to the test (open it, wait for its last block, run `stuckButtons` and `unfilledButtons`). Run it on the build before the fix and see it fail for the finding (`ecc-tdd-workflow`: RED counts only for the intended reason):

```bash
cd frontend && LANG=C.UTF-8 LC_ALL=C.UTF-8 DEMO_PASSWORD=… npx playwright test e2e/buttons.spec.ts --trace off
```

### 5. Fix

- In `frontend/src/styles.css`: the shared rule first (`.actions`, `.btn`, a theme's tokens), so every screen gains; a rule for one block only when the block is special, with a comment saying why. Comments in English, explaining why.
- In a component only the button's kind (`btn` → `btn btn-danger`, `btn` → `link-button`) or a container class (`<div className="actions">`). No colours, sizes or spacing in components; no inline `style`.
- Interface strings stay as they are; a label that is too long for a phone goes to the `copy-editor` agent or the `ecc-i18n-sync` skill.

### 6. Verify

`npm run build`, the screenshots again into `<scratchpad>/after`, the checklist on them, then `npx playwright test e2e/buttons.spec.ts e2e/forms.spec.ts` (the form test guards the alignment of fields) — the whole suite when the change is in a shared rule.

### 7. Report

In Russian, for the person: what was wrong (with the before screenshots), what changed and where (`file:line`), the after screenshots at 390px in the dark and the default theme, the test output RED → GREEN, and what was left as it was and why. Send the before/after images (`SendUserFile`) when the session can.

## Out of scope

- What is on a screen and how it is grouped — the `ecc-ux-reviewer` agent.
- The wording of labels and hints — the `copy-editor` agent.
- New colours outside the theme tokens, a new theme, fonts — a product decision for `docs/decisions.md`.
- Commit and PR only when the person says so.
