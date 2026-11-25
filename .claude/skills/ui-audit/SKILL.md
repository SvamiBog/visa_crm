---
name: ui-audit
description: "Measure and screenshot VisaCRM screens (or an HTML mockup) for visual consistency: one kind of control in one size (checkboxes, radios, fields, each kind of button, icons, headings), buttons 8px apart, nothing off a phone's screen, fonts and weights on the scale, the theme's own fonts, touch targets, clipped text, spacing on the 4px grid. Runs audit.cjs at 390 and 1280px in all four themes and writes report.md with screenshots. Used by the design-qa agent after a change to a screen and by screen-designer on a mockup; use it directly when a screen looks uneven or before a release."
---
# UI audit

The project's own skill (not from ECC). `CLAUDE.md` and `docs/decisions.md` («Отделка интерфейса», «Кнопки и отступы», «Отклик на нажатие») take precedence over this file.

The eye catches unfinished work before the mind names it: a small checkbox next to a big one, a field 36px high beside one 44px high, two buttons touching, a heading in a font the theme does not use. Reviewers reading code miss these; a screen at 390px in the dark theme shows them at once. `audit.cjs` opens each screen like the specialist does and measures what is on it.

## Run

The stand of the `ecc-e2e-testing` skill (built frontend served by the API with demo data; `npm run build` first, or the stand serves the old bundle). A cloud session needs `npm ci` in `frontend/` first; Chromium is installed.

```bash
# the screens the change touches (repeat --path); {case-client} — a demo client with a case
DEMO_PASSWORD=… node .claude/skills/ui-audit/audit.cjs --out <scratchpad>/audit \
  --base http://localhost:<port> --path /app/clients/{case-client} --path /app/dictionaries

# every main screen — when a shared style, a shared component or the layout changed
DEMO_PASSWORD=… node .claude/skills/ui-audit/audit.cjs --out <scratchpad>/audit --base … --preset key

# a form or block that opens on a button: press it before measuring (one --path)
DEMO_PASSWORD=… node .claude/skills/ui-audit/audit.cjs --out <scratchpad>/audit-payment --base … \
  --path /app/clients/{case-client} --open "+ Платёж"

# an HTML mockup (no stand needed)
node .claude/skills/ui-audit/audit.cjs --out <scratchpad>/mockup --file docs/prototype/<feature>.html
```

The audit sees a screen as it loads. Checkboxes, fields and buttons mostly live in forms and blocks that open on a press: audit each form the change adds or alters with `--open` (the button's, link's or tab's visible name; several `--open` are pressed in order). In Git Bash on Windows put `MSYS_NO_PATHCONV=1` before the command, or `/app/…` turns into a Windows path.

`--widths 390,1280`, `--themes blue,dark,warm,minimal`, `--lang ru|en` narrow or widen it. Run once with `--lang en` when labels changed: English is longer and breaks rows that fit in Russian. A screen the account cannot open (module off, redirect) is listed under «Не открылись», not silently skipped.

Output in `--out`: `<screen>/<theme>-<width>.png` (whole page), `report.md` (findings by screen) and `report.json`.

## What it measures

| Severity | Rule | What |
| --- | --- | --- |
| high | `size:*` | one kind of control in different sizes on one screen: checkboxes, radios, one-line fields and selects, main / ordinary / dangerous buttons (and `btn-sm` apart), icons in buttons, `h1`–`h3` |
| high | `stuck-buttons` | buttons side by side closer than 8px |
| high | `overflow` | the page wider than the screen, with the elements that stick out |
| high | `console-error` | errors in the browser console while the screen loads |
| medium | `font-size` | 13 or 15px outside monospace |
| medium | `font-weight` | heavier than 600 |
| medium | `font-family` | a font that is not the theme's (`--font-body`, `--font-heading`, `--font-num`, `--font-code`) |
| medium | `touch-target` | on a phone, a target under 24×24 with its label (WCAG 2.5.8); links inside a line of text are exempt |
| medium | `clipped-text` | text cut off without an ellipsis |
| low | `spacing` | a gap or margin off the 4px scale (10px and adjustments under 4px are allowed) |

Each finding names the element by its label, its card's heading and a short CSS path, so it can be found in the screenshot and in the code (`grep -n` for the class in `frontend/src/styles.css` and the component).

## Judge, do not just copy

The script measures; a person decides. Before reporting a finding:

- **Look at the screenshot.** A size difference can be deliberate: a compact field inside a table row beside a full form field, a large checkbox that is a row's only control. Deliberate is fine when the same role has the same size everywhere and the difference reads as intended; say why.
- **The same finding on every screen in one theme** comes from the theme's tokens (`frontend/src/styles.css`, `[data-ui-theme=…]`): report it once, as a theme finding.
- **Compare with neighbours.** A new screen's checkboxes, fields and buttons match the ones of the screens next to it (the card, the dictionaries, the settings); a mockup matches the app.
- What the script cannot see — colours outside the theme in code, inline `style`, a missing empty or error state, focus order, contrast — needs a look at the diff and the screenshots (`design-qa` covers it).

## Out of scope

What should be on a screen and how it is grouped is `ecc-ux-reviewer`'s ground; the wording is `copy-editor`'s; buttons and spacing fixes follow the `buttons-and-spacing` skill. This skill changes no project files.
