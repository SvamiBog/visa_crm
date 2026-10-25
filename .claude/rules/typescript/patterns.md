---
paths:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
---
# Frontend Patterns

> Adapted from ECC `rules/typescript/patterns.md` and `rules/react/patterns.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` takes precedence over this file.

## API access

- All requests go through `frontend/src/api.ts`: `apiGet`, `apiPost`, `apiPatch`, `apiPut`, `apiDelete` — same origin, cookies included, `/api` prefix (Vite proxies it to FastAPI in dev).
- A failure is an `ApiError` with the backend's code; show it as ``t(`errors.${errorCode(error)}`)``. Network failures give the code `network`.
- Files are fetched with `apiDownload` so an error shows on the page instead of being saved as the file.
- Personal data (dates of trips, names, documents) goes in request bodies, never in URLs — see the comment in `frontend/src/stay.ts`.

## Server state

- Server data lives in TanStack Query: `useQuery` with a key helper per resource (`tripsKey(contactId)` in `pages/clients/Trips.tsx`), `useMutation` that invalidates the keys it changes.
- Do not copy query data into `useState`; derive during render.
- Loading and error states for every query the user waits on.

## Forms

- Semantic `<form>` with `onSubmit` + `preventDefault()`; labels tied to inputs.
- Fields of a row line up even when a label wraps — `frontend/e2e/forms.spec.ts` checks this in both languages at several widths.

## Hooks

- Rules of hooks; complete dependency arrays; cleanup for listeners and timers.
- Effects only for synchronizing with the outside world, not for derived state.
