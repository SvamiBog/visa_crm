---
paths:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
  - "frontend/**/*.css"
  - "frontend/src/locales/*.json"
---
# TypeScript and React Coding Style

> Adapted from ECC `rules/typescript/coding-style.md` and `rules/react/coding-style.md` (github.com/affaan-m/ECC, MIT). Extends [../coding-style.md](../coding-style.md). `CLAUDE.md` («Темы», «i18n») takes precedence over this file.

## Types

- `strict` TypeScript with `noUnusedLocals` and `noUnusedParameters` (`frontend/tsconfig.json`); `npm run build` runs `tsc --noEmit` first.
- API shapes are `type` aliases with the backend's snake_case field names, next to their fetchers in the area module (`frontend/src/stay.ts`, `contacts.ts`, `vault.ts`); comments explain fields whose meaning is not obvious (`null: still there`).
- No `any`; `unknown` for caught errors, narrowed through `errorCode()` (`frontend/src/api.ts`). String literal unions instead of `enum`.
- Props typed with a named `type`; no `React.FC`.

## Strings and theme

- Every user-facing string goes through `t("…")` with keys in both `frontend/src/locales/ru.json` and `en.json` (skill `ecc-i18n-sync`).
- No colors, fonts or radii in components: only the theme variables `--c-*`, `--font-*`, `--radius-*` from `frontend/src/styles.css`, which define the four themes.

## Shape

- Pages in `frontend/src/pages/<area>/`, shared pieces in `frontend/src/components/`; a large page splits into parts files the way the folder already does (`ChecklistParts.tsx`, `StayParts.tsx`, `ui.tsx`).
- Function components and hooks only; state updates are immutable.
- No `console.log` in committed code.
- Formatting: there is no prettier in the project — match the surrounding code (double quotes, semicolons, 2-space indent, `.editorconfig`).
