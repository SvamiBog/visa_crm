---
name: ecc-i18n-sync
description: "Add or update interface strings in frontend/src/locales/ru.json and en.json together: Russian first, English complete, keys, plurals and placeholders in step, wording taken from where the string appears. Use when adding keys, changing interface copy or checking the locales for missing or stale strings."
---
> Adapted from ECC `skills/i18n-sync/SKILL.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# i18n Sync

Every interface string is a key `t("…")` in `frontend/src/locales/ru.json` and `en.json` (react-i18next, `frontend/src/i18n.ts`). Russian is the main language; English must be complete. This skill keeps both files in step without a second localization tool.

## The files

- Flat JSON objects of strings: the dotted key is one key (`"errors.document_date_in_future"`), grouped by prefix: `errors.<code>` for API error codes, `audit.action.<action>` for audit events, then one prefix per area (`contacts.`, `templates.`, …).
- Interpolation `{{name}}`; plurals by suffix with `count`: Russian `_one`, `_few`, `_many`, English `_one`, `_other`. So the key sets differ only by plural suffixes.
- `templates.*` strings that show document-template codes (`{{ФИО}}` / `{{Full name}}`) are translated on purpose: those codes come from `backend/app/core/templates.py`, not from i18next.

## Workflow

### 1. Worklist

For each key: the key, the Russian text, the English text or "missing", why it changes. A new API error code needs `errors.<code>`; a new `AuditAction` needs `audit.action.<action>`.

### 2. Context

Find where each key is used (`grep -rn '"<key>"' frontend/src`, or the error code in `backend/app/api/`). Read the component: is it a button, a heading, a field label, an error, an `aria-label`? Keep labels short: a form row must line up in both languages at narrow widths (`frontend/e2e/forms.spec.ts`).

### 3. Wording

- Russian: plain words of the visa specialist's trade, the terms of the glossary in `CLAUDE.md` (кейс, визовик, талон, регистратор), addressing the user as «вы».
- English: the same meaning in plain English; the code names of the glossary are not interface words (a case is "case", a specialist is "specialist").
- No personal data in strings or examples; placeholders stay exactly as in the other language.

### 4. Apply

Edit the JSON with precise edits next to the keys of the same group, keeping the files' formatting (2 spaces, UTF-8, Cyrillic as is, no escaping). Never rewrite a whole file through a script or embed JSON in a shell command.

### 5. Validate

```bash
python3 -I - <<'EOF'
import json, re
p = "frontend/src/locales/"
ru, en = (json.load(open(p + f, encoding="utf-8")) for f in ("ru.json", "en.json"))
base = lambda k: re.sub(r"_(one|few|many|other)$", "", k)
print("only in ru:", sorted({base(k) for k in ru} - {base(k) for k in en}))
print("only in en:", sorted({base(k) for k in en} - {base(k) for k in ru}))
ph = lambda s: set(re.findall(r"{{\s*(\w+)\s*}}", s))
print("placeholders differ:", [k for k in ru if k in en and not k.startswith("templates.") and ph(ru[k]) != ph(en[k])])
EOF
```

All three lists empty, then `npm run build` from `frontend/` if `node_modules` exists (otherwise say it was skipped). A form or layout string changed → the Playwright tests (`.claude/rules/ecc/typescript/testing.md`).

### 6. Report

Keys added or changed per file, wording choices, the validation output, and strings that need a native speaker's look.

## Out of scope

Pulling literal strings out of components (that is a code change reviewed with `/ecc-react-review`); visual checks beyond the Playwright tests.
