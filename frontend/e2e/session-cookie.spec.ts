import { expect, test, type Page } from "@playwright/test";

// The session cookie a neighbouring site cannot plant (backlog S18, security audit run-1,
// N10). Where the site is on HTTPS, the stands included, the browser keeps the session as
// __Host-visacrm_session: Secure, HttpOnly, SameSite=Lax, Path=/ and for this host alone,
// which no other site under the same parent domain can set. A cookie of the old plain name
// planted next to it changes nothing: the server keeps to the __Host- one. Chromium counts
// localhost as secure, so a stand on http://localhost (the ui job of CI) keeps it too.
// Signing out clears both names.

// The demo specialist and her employee (python -m app.cli seed-demo).
const EMAIL = process.env.E2E_EMAIL ?? "visa1@demo.visacrm.test";
const EMPLOYEE_EMAIL = process.env.E2E_EMPLOYEE_EMAIL ?? "visa1.staff@demo.visacrm.test";
const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const HOST_COOKIE = "__Host-visacrm_session";
const PLAIN_COOKIE = "visacrm_session";

type Answer = { status: number; body: { email?: string; mfa_required?: boolean } | null };

/** A request of the page itself, as the app sends it: the browser keeps and sends the
 * cookies by its own rules. */
async function call(page: Page, path: string, body?: object): Promise<Answer> {
  return page.evaluate(
    async ({ path, body }) => {
      const init: RequestInit = body
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }
        : {};
      const response = await fetch(`/api${path}`, init);
      return {
        status: response.status,
        body: response.status === 204 ? null : await response.json(),
      };
    },
    { path, body },
  );
}

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  // A stand marks its pages; a developer's machine (ENV=local) is on plain http and keeps
  // the plain name, which the backend tests cover.
  const env = await page.locator("html").getAttribute("data-env");
  test.skip(env !== "staging", "not a stand: the session cookie keeps its plain name");
  expect(PASSWORD, "DEMO_PASSWORD: the demo accounts' password").not.toBe("");
  const login = await call(page, "/auth/login", { email, password: PASSWORD });
  expect(login.status, JSON.stringify(login.body)).toBe(200);
  test.skip(Boolean(login.body?.mfa_required), "the demo accounts have two-factor sign-in on");
}

async function sessionCookies(page: Page) {
  return (await page.context().cookies()).filter((c) => c.name.endsWith(PLAIN_COOKIE));
}

test("the browser keeps the session for this host alone", async ({ page }) => {
  await signIn(page, EMAIL);
  const cookies = await sessionCookies(page);
  expect(cookies).toHaveLength(1);
  expect(cookies[0]).toMatchObject({
    name: HOST_COOKIE,
    domain: new URL(page.url()).hostname, // no leading dot: no Domain attribute
    path: "/",
    secure: true,
    httpOnly: true,
    sameSite: "Lax",
  });
  expect((await call(page, "/auth/me")).body?.email).toBe(EMAIL);
});

test("a plain-name cookie planted next to the session changes nothing", async ({
  page,
  browser,
  baseURL,
}) => {
  // The planted session is a real one: the employee's, signed in in another browser.
  const other = await browser.newContext({ baseURL });
  const otherPage = await other.newPage();
  await signIn(otherPage, EMPLOYEE_EMAIL);
  const [planted] = await sessionCookies(otherPage);
  expect(planted?.name).toBe(HOST_COOKIE);

  await signIn(page, EMAIL);
  await page.context().addCookies([{ name: PLAIN_COOKIE, value: planted.value, url: baseURL }]);
  expect((await sessionCookies(page)).map((c) => c.name).sort()).toEqual([
    HOST_COOKIE,
    PLAIN_COOKIE,
  ]);
  expect((await call(page, "/auth/me")).body?.email).toBe(EMAIL);

  // Signing out clears both names.
  expect((await call(page, "/auth/logout", {})).status).toBe(204);
  expect(await sessionCookies(page)).toEqual([]);
  expect((await call(page, "/auth/me")).status).toBe(401);

  await call(otherPage, "/auth/logout", {});
  await other.close();
});
