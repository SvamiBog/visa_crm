---
name: ecc-i18n-sync
description: "Add and synchronize VisaCRM's interface strings in frontend/src/locales/ru.json and en.json: every key in both languages, API error codes in errors.*, audit actions in audit.action.*, i18next plurals and placeholders intact. Use when adding UI text, an error code, an audit action, or reviewing missing translations."
metadata:
  origin: community
---

> Adapted from ECC `skills/i18n-sync/SKILL.md` v2.2.3 (github.com/affaan-m/ECC, MIT; see `.claude/ECC-NOTICE.md`). `CLAUDE.md` and `.claude/rules/` take precedence over this file.
>
> VisaCRM: two flat JSON files with dotted keys, Russian is the source and English must be complete (`CLAUDE.md` → «i18n»). The optional Locakit tooling of the original is removed: nothing is installed for this skill.

# i18n Sync

Add or update interface strings by reading where each key appears in the product. Preserve the files' structure, interpolation syntax and unrelated translations.

## When to Activate

- A component gets new user-facing text
- The backend gets a new error code (`HTTPException(..., "<code>")`) — the UI shows it as ``t(`errors.${code}`)``
- `AuditAction` gets a new value — its label is `audit.action.<value>` (for example `cases.package_item_marked`)
- A module or a dictionary gets a user-facing name
- Reviewing missing or stale translations

## The Files

- `frontend/src/locales/ru.json` — source; `frontend/src/locales/en.json` — complete English
- Flat keys with dots: `"auth.signIn": "Войти"`; grouped by area prefix (`auth.`, `clients.`, `errors.`, `audit.action.`)
- Interpolation: `{{count}}`, `{{name}}` — never translated, same names in both files
- Plurals (i18next): Russian `_one`, `_few`, `_many` (and `_other` where present); English `_one`, `_other`. The two files have different suffixes for the same base key — that is correct
- Terms: follow `CLAUDE.md` → «Глоссарий» for the Russian product words (визовик, кейс, талон, регистратор…) and keep the English wording already used for the same term in `en.json`

## Workflow

### 1. Establish the worklist
For each item: key, Russian text, English text, where it is used. Search the code for the key (`t("…")`) and for neighbouring keys of the same screen.

### 2. Gather usage context
Read the component: button label, heading, error, `aria-label`, fragment of a sentence. Note length limits — form labels must fit the grid at every width (`frontend/e2e/forms.spec.ts` checks both languages).

### 3. Write both languages
Russian first (the product's language), then English in the register already used in `en.json`. Keep placeholders and plural forms.

### 4. Apply and validate
Edit the JSON files with precise edits (the Edit tool), next to the keys of the same area; never rewrite a whole file or pipe JSON through the shell. Then check parity — the same base keys in both files once plural suffixes are stripped, and the same placeholders:

```bash
python -c "import json,re,sys; f=lambda p:json.load(open(p,encoding='utf-8')); b=lambda d:{re.sub(r'_(zero|one|two|few|many|other)$','',k) for k in d}; ru,en=f(sys.argv[1]),f(sys.argv[2]); print('only ru:',sorted(b(ru)-b(en))); print('only en:',sorted(b(en)-b(ru)))" frontend/src/locales/ru.json frontend/src/locales/en.json
```

Both lists must be empty. `npm run build` checks that the files still parse.

### 5. Report
Changed keys, terminology choices, the parity result, and strings that need a native speaker's look (legal or client-facing wording).

## Out of Scope

- Moving hard-coded strings of other screens into the locales (separate task)
- New languages (a product decision)
- Visual QA beyond what the browser tests check
