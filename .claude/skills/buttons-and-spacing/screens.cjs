// Screenshots of one VisaCRM screen for the buttons-and-spacing skill: the whole page,
// its header and every card on its own, at each width and in each theme, with the
// phone's tab bar hidden so it covers nothing.
//
//   node .claude/skills/buttons-and-spacing/screens.cjs --out DIR --path /app/clients/{case-client}
//
// Options (all but --out have defaults):
//   --path    the screen; {case-client} — a demo client with a case, {plain-client} —
//             a client without history (the card shows «Удалить карточку»)
//   --widths  390,1280      --themes blue,dark,warm,minimal      --lang ru
//   --base    http://localhost:8000 (E2E_BASE_URL)
//   --email   visa1@demo.visacrm.test (E2E_EMAIL); the password is DEMO_PASSWORD
// Needs the stand of the ecc-e2e-testing skill: the built frontend served by the API
// with demo data. Files: DIR/<theme>-<width>-page.png, -header.png, -NN-<block>.png.
const path = require("node:path");
const fs = require("node:fs");

const { chromium } = require(
  require.resolve("playwright", { paths: [path.join(__dirname, "../../../frontend")] }),
);

const args = Object.fromEntries(
  process.argv
    .slice(2)
    .reduce((pairs, arg, i, all) => (arg.startsWith("--") ? [...pairs, [arg.slice(2), all[i + 1]]] : pairs), []),
);
const OUT = args.out;
if (!OUT) {
  console.error("--out DIR is required");
  process.exit(2);
}
const PATH = args.path ?? "/app/clients/{case-client}";
const WIDTHS = (args.widths ?? "390,1280").split(",").map(Number);
const THEMES = (args.themes ?? "blue,dark,warm,minimal").split(",");
const LANG = args.lang ?? "ru";
const BASE = args.base ?? process.env.E2E_BASE_URL ?? "http://localhost:8000";
const EMAIL = args.email ?? process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD;
if (!PASSWORD) {
  console.error("DEMO_PASSWORD: the demo accounts' password");
  process.exit(2);
}

/** The {case-client} and {plain-client} of --path, found through the API. */
async function resolvePath(request) {
  if (!PATH.includes("{")) return PATH;
  const found = await request.post("/api/contacts/search", { data: {} });
  const ids = { "case-client": "", "plain-client": "" };
  for (const { id } of (await found.json()).items) {
    const card = await (await request.get(`/api/contacts/${id}`)).json();
    if (!ids["case-client"] && card.cases?.length) ids["case-client"] = id;
    if (!ids["plain-client"] && card.can_delete) ids["plain-client"] = id;
  }
  return PATH.replace(/\{(case-client|plain-client)\}/g, (_, key) => {
    if (!ids[key]) throw new Error(`The demo has no ${key}`);
    return ids[key];
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  for (const width of WIDTHS) {
    const phone = width < 720;
    const context = await browser.newContext({
      baseURL: BASE,
      viewport: { width, height: phone ? 844 : 900 },
      isMobile: phone,
      hasTouch: phone,
    });
    const page = await context.newPage();
    await page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), LANG);
    const login = await page.request.post("/api/auth/login", {
      data: { email: EMAIL, password: PASSWORD },
    });
    if (!login.ok()) throw new Error(`login: ${login.status()}`);
    await page.goto(await resolvePath(page.request));
    await page.locator("h1").first().waitFor();
    await page.waitForLoadState("networkidle");
    for (const theme of THEMES) {
      // In place, like the settings preview: the account's own theme is not changed.
      await page.evaluate((th) => {
        document.documentElement.dataset.uiTheme = th;
        document.querySelectorAll(".tabbar").forEach((bar) => (bar.style.display = "none"));
      }, theme);
      const name = `${theme}-${width}`;
      await page.screenshot({ path: `${OUT}/${name}-page.png`, fullPage: true });
      const header = page.locator(".page-header").first();
      if (await header.count()) await header.screenshot({ path: `${OUT}/${name}-header.png` });
      const blocks = page.locator(".page .card");
      for (let i = 0; i < (await blocks.count()); i++) {
        const block = blocks.nth(i);
        if (!(await block.isVisible())) continue;
        const title = (await block.locator("h2, h3, h4").first().textContent().catch(() => "")) ?? "";
        const slug = title.trim().slice(0, 24).replace(/[^\p{L}\p{N}]+/gu, "_") || "card";
        await block.screenshot({ path: `${OUT}/${name}-${String(i).padStart(2, "0")}-${slug}.png` });
      }
    }
    await context.close();
  }
  await browser.close();
  console.log(`screenshots in ${OUT}`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
