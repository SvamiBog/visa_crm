---
paths:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
---
# Frontend Security

> Adapted from ECC `rules/typescript/security.md` and `rules/react/security.md` (github.com/affaan-m/ECC, MIT). Extends [../security.md](../security.md).

- No secrets in the bundle: nothing private in `VITE_*` variables or source.
- The session is an httpOnly cookie set by the API; `localStorage` holds only UI preferences such as the language (`frontend/src/i18n.ts`), wrapped in `try` like there.
- No client data in URLs or query strings — ids only; personal data in request bodies.
- No `dangerouslySetInnerHTML`; links built from user data validate the scheme; `target="_blank"` with `rel="noopener noreferrer"`.
- A vault password is fetched only by an explicit «show» or «copy» (a POST mutation), kept in component state, never in the query cache, and hides itself after a timeout — as `frontend/src/pages/vault/Vault.tsx` does.
- Passport reading (MRZ, `frontend/src/ocr.ts`) runs in the browser: the photo never leaves the device, only the recognised lines go to the API, and the OCR engine is served from this site, not a CDN. Keep it that way.
- CI runs `npm audit --omit=dev --audit-level=high`; a new dependency needs the user's agreement.
