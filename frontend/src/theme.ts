// Interface styles from docs/prototype/Styles.dc.html; "blue" (A) is the default.
// The choice is stored in the user's account (PATCH /api/auth/preferences) and mirrored
// in localStorage so the login screen opens in the last used style.
export const THEMES = ["blue", "dark", "warm", "minimal"] as const;
export type UiTheme = (typeof THEMES)[number];

export const DEFAULT_THEME: UiTheme = "blue";
const STORAGE_KEY = "visacrm.theme";

function isTheme(value: unknown): value is UiTheme {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

export function applyTheme(theme: string) {
  const value = isTheme(theme) ? theme : DEFAULT_THEME;
  document.documentElement.dataset.uiTheme = value;
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // private mode or blocked storage: the account still keeps the choice
  }
}

export function applySavedTheme() {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch {
    // no storage: default theme
  }
  applyTheme(saved ?? DEFAULT_THEME);
}
