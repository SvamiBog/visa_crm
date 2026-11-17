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

/** A phone colours its status bar and the browser's toolbar by `theme-color`: it takes
    the page's background of the chosen style, so the dark style has no white bar. */
function syncBrowserColour() {
  const colour = getComputedStyle(document.documentElement).getPropertyValue("--c-bg").trim();
  if (!colour) return;
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.append(meta);
  }
  meta.content = colour;
}

export function applyTheme(theme: string) {
  const value = isTheme(theme) ? theme : DEFAULT_THEME;
  document.documentElement.dataset.uiTheme = value;
  syncBrowserColour();
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
