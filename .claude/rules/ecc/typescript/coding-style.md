---
paths:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
---
> Adapted from ECC `rules/typescript/coding-style.md`, `patterns.md` and `security.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# TypeScript Coding Style

- `frontend/tsconfig.json` is strict with `noUnusedLocals` and `noUnusedParameters`; `npm run build` must pass. The project has no ESLint or Prettier: keep the surrounding formatting (2 spaces, double quotes, semicolons).
- Object shapes are `type X = { … }` (the code has no `interface`); `as const` arrays and string unions instead of `enum` (`MESSENGERS` in `frontend/src/contacts.ts`); no `any` — `unknown`, then narrow.
- Explicit types on exported functions; props typed with a named type; function components without `React.FC`.
- Server state through TanStack Query hooks in the area module (`useContactLists` in `frontend/src/contacts.ts`); requests only through `frontend/src/api.ts` (`apiGet`, `apiPost`, …, `apiDownload`), which throws `ApiError` carrying the API's code.
- Errors shown as ``t(`errors.${errorCode(error)}`, { defaultValue: t("errors.unknown") })``.
- No interface strings in code: `t("…")` keys added to `frontend/src/locales/ru.json` and `en.json` together.
- No colours, fonts or radii in components: theme variables `--c-*`, `--font-*`, `--radius-*` from `frontend/src/styles.css`.
- State updated immutably (spread, `map`, `filter`); query data is never mutated.
- No `console.log`; `localStorage` holds UI preferences only (the theme), never tokens or personal data.
