// Visual consistency audit of VisaCRM screens for the design-qa and screen-designer
// agents (docs/process.md): screenshots of every screen at each width and in each theme,
// and measurements of what the eye reads as unfinished work — one kind of control in
// different sizes (a small checkbox next to a big one), fonts off the scale, buttons
// stuck together, content running off a phone's screen.
//
//   node .claude/skills/ui-audit/audit.cjs --out DIR --path /app/clients/{case-client} [--path …]
//   node .claude/skills/ui-audit/audit.cjs --out DIR --preset key        # the main screens
//   node .claude/skills/ui-audit/audit.cjs --out DIR --file docs/prototype/<mockup>.html
//
// Options (all but --out have defaults):
//   --path    a screen, repeatable; {case-client} — a demo client with a case,
//             {plain-client} — a client without history
//   --preset  key — every main screen of the specialist (a change to a shared style)
//   --file    a local HTML mockup (no login), repeatable
//   --open    the name of a button or link to press after the screen loads, before the
//             measuring — opens a form, a block or a tab; repeatable, pressed in order on
//             every --path (so give one --path with it)
//   --widths  390,1280      --themes blue,dark,warm,minimal      --lang ru
//   --base    http://localhost:8000 (E2E_BASE_URL)
//   --email   visa1@demo.visacrm.test (E2E_EMAIL); the password is DEMO_PASSWORD
// Needs the stand of the ecc-e2e-testing skill for --path / --preset: the built frontend
// served by the API with demo data. Writes DIR/<screen>/<theme>-<width>.png,
// DIR/report.json and DIR/report.md, and prints the summary. Exit 0 whatever it finds:
// the agent judges the findings; exit 1 only when the audit itself could not run.
"use strict";

const path = require("node:path");
const fs = require("node:fs");
const { pathToFileURL } = require("node:url");

const FRONTEND = path.join(__dirname, "../../../frontend");
const { chromium } = require(require.resolve("playwright", { paths: [FRONTEND] }));

const KEY_SCREENS = [
  "/app",
  "/app/clients",
  "/app/clients/{case-client}",
  // The card's cases are on a tab of their own (backlog 1.15.17).
  "/app/clients/{case-client}?tab=cases",
  "/app/queues",
  "/app/bookings",
  "/app/vault",
  "/app/analytics",
  "/app/checklists",
  "/app/templates",
  "/app/dictionaries",
  "/app/employees",
  "/app/stay-calculator",
  "/app/import",
  "/settings",
];

/** --name value pairs; a repeated name collects every value. */
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith("--")) continue;
    const name = argv[i].slice(2);
    (out[name] ??= []).push(argv[i + 1]);
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const last = (name, fallback) => (args[name] ? args[name][args[name].length - 1] : fallback);
const OUT = last("out");
if (!OUT) {
  console.error("--out DIR is required");
  process.exit(1);
}
const PATHS = [...(args.path ?? []), ...(last("preset") === "key" ? KEY_SCREENS : [])];
const FILES = args.file ?? [];
const OPEN = args.open ?? [];
const mangled = PATHS.find((p) => !p.startsWith("/"));
if (mangled) {
  // Git Bash on Windows turns an argument like /app/clients into C:/Program Files/Git/app/clients.
  console.error(`--path must start with "/": got "${mangled}". In Git Bash run with MSYS_NO_PATHCONV=1.`);
  process.exit(1);
}
if (!PATHS.length && !FILES.length) {
  console.error("Give at least one --path, --preset key or --file");
  process.exit(1);
}
const WIDTHS = last("widths", "390,1280").split(",").map(Number);
const THEMES = last("themes", "blue,dark,warm,minimal").split(",");
const LANG = last("lang", "ru");
const BASE = last("base", process.env.E2E_BASE_URL ?? "http://localhost:8000");
const EMAIL = last("email", process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test");
const PASSWORD = process.env.DEMO_PASSWORD;
if (PATHS.length && !PASSWORD) {
  console.error("DEMO_PASSWORD: the demo accounts' password");
  process.exit(1);
}

/** The {case-client} and {plain-client} of a path, found through the API. */
async function resolvePath(request, screen, cache) {
  if (!screen.includes("{")) return screen;
  if (!cache.ids) {
    cache.ids = { "case-client": "", "plain-client": "" };
    // Demo clients only (their Telegram is demo_<n>_<m>): cards other tests make
    // may be deleted under the audit.
    const found = await request.post("/api/contacts/search", { data: { query: "demo_" } });
    for (const { id } of (await found.json()).items ?? []) {
      const card = await (await request.get(`/api/contacts/${id}`)).json();
      if (!cache.ids["case-client"] && card.cases?.length) cache.ids["case-client"] = id;
      if (!cache.ids["plain-client"] && card.can_delete) cache.ids["plain-client"] = id;
    }
  }
  return screen.replace(/\{(case-client|plain-client)\}/g, (_, key) => {
    if (!cache.ids[key]) throw new Error(`The demo has no ${key}`);
    return cache.ids[key];
  });
}

/**
 * Runs in the page. Measures the visible screen and returns findings
 * {severity, rule, message, examples[]}. Severity: high — the eye sees it at once
 * (one kind of control in different sizes, buttons stuck together, content off the
 * screen); medium — off the design rules of docs/decisions.md («Отделка интерфейса»,
 * «Кнопки и отступы»); low — worth a look.
 */
function measure(phone) {
  const vw = document.documentElement.clientWidth;
  const round = (n) => Math.round(n * 10) / 10;
  const findings = [];
  const add = (severity, rule, message, examples) =>
    findings.push({ severity, rule, message, examples: examples.slice(0, 6) });

  const shown = (el) => {
    if (el.closest("[hidden], [aria-hidden='true'], .tabbar")) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && Number(s.opacity) > 0.05;
  };
  const text = (value) => (value || "").trim().replace(/\s+/g, " ").slice(0, 40);
  const name = (el) =>
    text(
      el.getAttribute("aria-label") ||
        (el.labels && el.labels[0] && el.labels[0].textContent) ||
        el.closest("label")?.textContent ||
        el.getAttribute("placeholder") ||
        el.textContent ||
        el.getAttribute("name"),
    ) || "(без подписи)";
  const where = (el) => {
    const parts = [];
    for (let node = el; node && node !== document.body && parts.length < 3; node = node.parentElement) {
      const cls = [...node.classList].slice(0, 2).join(".");
      parts.unshift(node.tagName.toLowerCase() + (cls ? "." + cls : ""));
    }
    return parts.join(" > ");
  };
  const block = (el) => {
    const card = el.closest(".card, section, form, header, nav, dialog");
    const head = card && card.querySelector("h1, h2, h3, h4, legend");
    return head ? text(head.textContent) : "";
  };
  const example = (el, extra) =>
    `«${name(el)}»${block(el) ? " в «" + block(el) + "»" : ""} — ${extra} (${where(el)})`;
  const all = (selector) => [...document.querySelectorAll(selector)].filter(shown);

  // 1. One kind of control, one size.
  const sizeGroups = [
    ["checkbox", "галочки (checkbox)", all("input[type=checkbox]"), "both"],
    ["radio", "переключатели (radio)", all("input[type=radio]"), "both"],
    [
      "field",
      "поля ввода и списки в одну строку",
      all(
        "input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=file]):not([type=range]):not([type=color]), select:not([multiple])",
      ),
      "height",
    ],
  ];
  const buttons = all("button, a.btn, [role=button]");
  const kindOf = (el) => {
    if (el.classList.contains("link-button")) return null;
    if (!el.classList.contains("btn")) return null;
    const kind = el.classList.contains("btn-p")
      ? "главные"
      : el.classList.contains("btn-danger")
        ? "опасные"
        : "обычные";
    return `${kind} кнопки${el.classList.contains("btn-sm") ? " (btn-sm)" : ""}`;
  };
  const byKind = new Map();
  for (const el of buttons) {
    const kind = kindOf(el);
    if (!kind) continue;
    if (!byKind.has(kind)) byKind.set(kind, []);
    byKind.get(kind).push(el);
  }
  for (const [kind, list] of byKind) sizeGroups.push([`button:${kind}`, kind, list, "height"]);
  const icons = all(".btn svg, .link-button svg, button svg");
  sizeGroups.push(["icon", "иконки в кнопках", icons, "both"]);
  for (const tag of ["h1", "h2", "h3"]) {
    const heads = all(tag);
    sizeGroups.push([`heading:${tag}`, `заголовки ${tag}`, heads, "font"]);
  }

  for (const [rule, label, list, dim] of sizeGroups) {
    if (list.length < 2) continue;
    const sizes = new Map();
    for (const el of list) {
      const r = el.getBoundingClientRect();
      const key =
        dim === "font"
          ? getComputedStyle(el).fontSize
          : dim === "height"
            ? `высота ${round(r.height)}px`
            : `${round(r.width)}×${round(r.height)}px`;
      if (!sizes.has(key)) sizes.set(key, []);
      sizes.get(key).push(el);
    }
    if (sizes.size < 2) continue;
    // Sizes within half a pixel are one size (sub-pixel rounding).
    const values = [...sizes.keys()].map((k) => k.match(/[\d.]+/g).map(Number));
    const spread = Math.max(
      ...values[0].map((_, i) => Math.max(...values.map((v) => v[i])) - Math.min(...values.map((v) => v[i]))),
    );
    if (spread <= 0.5) continue;
    const ranked = [...sizes.entries()].sort((a, b) => b[1].length - a[1].length);
    const [usual] = ranked[0];
    add(
      "high",
      `size:${rule}`,
      `${label} разного размера: ${ranked.map(([k, els]) => `${k} — ${els.length}`).join(", ")}; обычный — ${usual}`,
      ranked.slice(1).flatMap(([k, els]) => els.map((el) => example(el, k))),
    );
  }

  // 2. Buttons side by side keep a gap of 8px (decisions «Кнопки и отступы»).
  const btns = buttons.filter((el) => el.classList.contains("btn"));
  const stuck = [];
  for (let i = 0; i < btns.length; i++) {
    for (let j = i + 1; j < btns.length; j++) {
      const a = btns[i].getBoundingClientRect();
      const b = btns[j].getBoundingClientRect();
      const overlapY = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (overlapY < Math.min(a.height, b.height) / 2) continue;
      const gap = a.left < b.left ? b.left - a.right : a.left - b.right;
      if (gap < 7.5) stuck.push(`«${name(btns[i])}» и «${name(btns[j])}» — зазор ${round(gap)}px (${where(btns[i])})`);
    }
  }
  if (stuck.length) add("high", "stuck-buttons", `кнопки без зазора 8px: ${stuck.length}`, stuck);

  // 3. Nothing runs off the screen.
  if (document.documentElement.scrollWidth > vw + 1) {
    const scrolls = (el) => {
      for (let node = el.parentElement; node; node = node.parentElement) {
        const o = getComputedStyle(node).overflowX;
        if (o === "auto" || o === "scroll" || o === "hidden") return true;
      }
      return false;
    };
    const off = all("body *")
      .filter((el) => el.getBoundingClientRect().right > vw + 1 && !scrolls(el))
      .filter((el) => ![...el.children].some((c) => c.getBoundingClientRect().right > vw + 1));
    add(
      "high",
      "overflow",
      `страница шире экрана: ${document.documentElement.scrollWidth}px при ширине ${vw}px`,
      off.map((el) => example(el, `правый край ${round(el.getBoundingClientRect().right)}px`)),
    );
  }

  // 4. Type on the scale: no 13 and 15px outside monospace, weight up to 600, the theme's
  // own fonts (decisions «Отделка интерфейса»): --font-body, --font-heading, --font-num,
  // --font-code of the theme in force.
  const family = (value) => value.replace(/["']/g, "").replace(/\s*,\s*/g, ",").trim();
  const bodyFont = getComputedStyle(document.body).fontFamily;
  const root = getComputedStyle(document.documentElement);
  const themeFonts = new Set(
    [bodyFont, ...["--font-body", "--font-heading", "--font-num", "--font-code"].map((v) => root.getPropertyValue(v))]
      .filter(Boolean)
      .map(family),
  );
  const fonts = new Map();
  const offScale = [];
  const heavy = [];
  for (const el of all("body *")) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const s = getComputedStyle(el);
    const mono = /mono|courier|consolas/i.test(s.fontFamily);
    const size = parseFloat(s.fontSize);
    if (!mono && (Math.abs(size - 13) < 0.25 || Math.abs(size - 15) < 0.25)) {
      offScale.push(example(el, `${s.fontSize}`));
    }
    if (Number(s.fontWeight) > 600) heavy.push(example(el, `вес ${s.fontWeight}`));
    if (!mono && !themeFonts.has(family(s.fontFamily))) {
      if (!fonts.has(s.fontFamily)) fonts.set(s.fontFamily, []);
      fonts.get(s.fontFamily).push(el);
    }
  }
  if (offScale.length) add("medium", "font-size", `шрифт 13 или 15px вне шкалы: ${offScale.length}`, offScale);
  if (heavy.length) add("medium", "font-weight", `жирность больше 600: ${heavy.length}`, heavy);
  for (const [font, els] of fonts) {
    add("medium", "font-family", `шрифт не из темы «${font}» (у темы «${[...themeFonts].join(" | ")}»): ${els.length}`, els.map((el) => example(el, font)));
  }

  // 5. On a phone a target is at least 24×24 (WCAG 2.5.8), counting its label. A link
  // inside a line of text is exempt there (its size is the line's).
  if (phone) {
    const small = [];
    for (const el of all("button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button]")) {
      if (el.tagName === "A" && getComputedStyle(el).display === "inline") continue;
      const r = (el.closest("label") || el).getBoundingClientRect();
      if (r.width < 24 || r.height < 24) small.push(example(el, `${round(r.width)}×${round(r.height)}px`));
    }
    if (small.length) add("medium", "touch-target", `цели нажатия меньше 24×24 на телефоне: ${small.length}`, small);
  }

  // 6. Text cut off without an ellipsis.
  const clipped = all("body *").filter((el) => {
    const s = getComputedStyle(el);
    return (
      s.overflowX === "hidden" &&
      s.textOverflow !== "ellipsis" &&
      el.scrollWidth > el.clientWidth + 1 &&
      [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())
    );
  });
  if (clipped.length) add("medium", "clipped-text", `текст обрезан без многоточия: ${clipped.length}`, clipped.map((el) => example(el, "обрезан")));

  // 7. Spaces from the 4px scale; 10px only between an icon and its label, and
  // adjustments under 4px are allowed (decisions «Отделка интерфейса»).
  const spaces = new Map();
  for (const el of all(".page *, .content *")) {
    const s = getComputedStyle(el);
    const values = [];
    if (s.display.includes("flex") || s.display.includes("grid")) values.push(["gap", s.rowGap], ["gap", s.columnGap]);
    values.push(["margin-top", s.marginTop], ["margin-bottom", s.marginBottom]);
    for (const [prop, raw] of values) {
      const v = parseFloat(raw);
      if (!Number.isFinite(v) || Math.abs(v) < 4 || Math.abs(v) % 4 < 0.05 || v === 10) continue;
      const key = `${prop} ${raw}`;
      if (!spaces.has(key)) spaces.set(key, []);
      spaces.get(key).push(el);
    }
  }
  for (const [key, els] of spaces) {
    add("low", "spacing", `отступ не из шкалы 4px: ${key} — ${els.length}`, els.map((el) => example(el, key)));
  }

  return findings;
}

/**
 * The whole page in one shot. Not `fullPage: true`: in Chromium it drops the touch
 * emulation, so (pointer: coarse) stops matching and a phone's 44px targets shrink to
 * their desktop size in that shot and in every measure after it (found 2026-11-25:
 * «Изменить» in «Финансы» measured 17px). The viewport grows to the page's height for the
 * shot and goes back, which keeps the emulation.
 */
async function shootWholePage(page, file, width) {
  const size = page.viewportSize();
  const height = await page.evaluate(() =>
    Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0),
  );
  await page.setViewportSize({ width, height: Math.max(height, size.height) });
  await page.screenshot({ path: file });
  await page.setViewportSize(size);
}

function slug(value) {
  return value.replace(/^\/+/, "").replace(/[^\p{L}\p{N}]+/gu, "_").replace(/_+$/, "") || "home";
}

async function auditScreen(browser, screen, results) {
  for (const width of WIDTHS) {
    const phone = width < 720;
    const context = await browser.newContext({
      baseURL: screen.file ? undefined : BASE,
      viewport: { width, height: phone ? 844 : 900 },
      isMobile: phone,
      hasTouch: phone,
    });
    const page = await context.newPage();
    const errors = [];
    page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text().slice(0, 200)));
    page.on("pageerror", (err) => errors.push(String(err).slice(0, 200)));
    let url;
    try {
      if (screen.file) {
        url = pathToFileURL(path.resolve(screen.file)).href;
        await page.goto(url);
        await page.waitForLoadState("load");
      } else {
        await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), LANG);
        const login = await page.request.post("/api/auth/login", { data: { email: EMAIL, password: PASSWORD } });
        if (!login.ok()) throw new Error(`login: ${login.status()}`);
        url = await resolvePath(page.request, screen.path, results.cache);
        await page.goto(url);
        await page.locator("h1").first().waitFor({ timeout: 15000 });
        await page.waitForLoadState("networkidle");
        if (new URL(page.url()).pathname !== new URL(url, BASE).pathname) {
          throw new Error(`redirected to ${new URL(page.url()).pathname} (module off for this account?)`);
        }
        for (const label of OPEN) {
          const target = page
            .getByRole("button", { name: label })
            .or(page.getByRole("link", { name: label }))
            .or(page.getByRole("tab", { name: label }))
            .first();
          await target.click({ timeout: 10000 });
          await page.waitForLoadState("networkidle");
          await page.waitForTimeout(300); // an opening block settles (decisions «Отклик на нажатие»)
        }
      }
    } catch (error) {
      results.skipped.push(`${screen.name} @${width}: ${error.message}`);
      await context.close();
      continue;
    }
    const dir = path.join(OUT, screen.name);
    fs.mkdirSync(dir, { recursive: true });
    for (const theme of THEMES) {
      // In place, like the settings preview: the account's own theme is not changed. The
      // phone's tab bar is fixed: on a full-page shot it would cover the middle of the page.
      await page.evaluate((th) => {
        document.documentElement.dataset.uiTheme = th;
        document.querySelectorAll(".tabbar").forEach((bar) => (bar.style.display = "none"));
      }, theme);
      await page.evaluate(() => document.fonts.ready);
      // Measure first, in the phone's own viewport, then take the shot.
      const measured = await page.evaluate(measure, phone);
      await shootWholePage(page, path.join(dir, `${theme}-${width}.png`), width);
      for (const finding of measured) {
        const key = `${screen.name}|${width}|${finding.rule}|${finding.message}`;
        const seen = results.byKey.get(key);
        if (seen) seen.themes.push(theme);
        else {
          const entry = { screen: screen.name, url, width, themes: [theme], ...finding };
          results.byKey.set(key, entry);
          results.findings.push(entry);
        }
      }
    }
    if (errors.length) {
      results.findings.push({
        screen: screen.name,
        url,
        width,
        themes: THEMES,
        severity: "high",
        rule: "console-error",
        message: `ошибки в консоли браузера: ${errors.length}`,
        examples: [...new Set(errors)].slice(0, 6),
      });
    }
    results.shots.push(`${screen.name} @${width}`);
    await context.close();
  }
}

function report(results) {
  const order = { high: 0, medium: 1, low: 2 };
  const findings = results.findings.sort(
    (a, b) => a.screen.localeCompare(b.screen) || order[a.severity] - order[b.severity] || a.width - b.width,
  );
  const count = (s) => findings.filter((f) => f.severity === s).length;
  const lines = [
    "# Аудит интерфейса",
    "",
    `Экраны: ${results.shots.length} снимков (ширины ${WIDTHS.join(", ")}, темы ${THEMES.join(", ")}, язык ${LANG}).`,
    `Находки: высокие — ${count("high")}, средние — ${count("medium")}, низкие — ${count("low")}.`,
    "",
  ];
  if (results.skipped.length) {
    lines.push("## Не открылись", "", ...results.skipped.map((s) => `- ${s}`), "");
  }
  let screen = null;
  for (const f of findings) {
    if (f.screen !== screen) {
      screen = f.screen;
      lines.push(`## ${screen}`, "");
    }
    const themes = f.themes.length === THEMES.length ? "все темы" : f.themes.join(", ");
    lines.push(`- **${f.severity}** · ${f.width}px · ${themes} · ${f.message}`);
    for (const e of f.examples) lines.push(`  - ${e}`);
  }
  if (!findings.length) lines.push("Находок нет.");
  fs.writeFileSync(path.join(OUT, "report.md"), lines.join("\n") + "\n");
  fs.writeFileSync(
    path.join(OUT, "report.json"),
    JSON.stringify({ widths: WIDTHS, themes: THEMES, lang: LANG, shots: results.shots, skipped: results.skipped, findings }, null, 2),
  );
  console.log(lines.slice(0, 5).join("\n"));
  console.log(`report: ${path.join(OUT, "report.md")}`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const screens = [
    ...PATHS.map((p) => ({ path: p, name: slug([p, ...OPEN].join("_")) })),
    ...FILES.map((f) => ({ file: f, name: slug(path.basename(f, ".html")) })),
  ];
  const results = { findings: [], byKey: new Map(), skipped: [], shots: [], cache: {} };
  const browser = await chromium.launch();
  for (const screen of screens) await auditScreen(browser, screen, results);
  await browser.close();
  report(results);
  if (!results.shots.length) process.exit(1);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
