import { chromium } from "playwright";

// Checks the URL routing: /, /c/:id, /c/:id/page and ?settings=<section>, with
// the session, chat list and stream mocked. Needs `npm run preview` on :4173.
const BASE = "http://localhost:4173";
const browser = await chromium.launch();

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}: ${name}`, detail);
  if (!ok) failures += 1;
}

const json = (body, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const PAGE_HTML = "<!doctype html><html><body><h1>routing page</h1></body></html>";
const PREVIEW_BASE = "/api/v1/webpages/webpage-route/preview/key-check/";
const PREVIEW_URL = `${PREVIEW_BASE}index.html`;

// A has a webpage (so the top bar shows the canvas toggle), B is plain.
const chatA = {
  id: "A",
  title: "Alpha chat",
  mode: "chat",
  subject: null,
  messages: [
    { id: "a-u", role: "user", content: "make an alpha page" },
    {
      id: "a-a",
      parentId: "a-u",
      role: "assistant",
      content: "Alpha reply text",
      parts: [
        { type: "text", text: "Alpha reply text" },
        { type: "webpage", webpageId: "webpage-route", path: "index.html", files: ["index.html"] },
      ],
    },
  ],
};
const chatB = {
  id: "B",
  title: "Beta chat",
  mode: "chat",
  subject: null,
  messages: [
    { id: "b-u", role: "user", content: "hello beta" },
    { id: "b-a", parentId: "b-u", role: "assistant", content: "Beta reply text", parts: [{ type: "text", text: "Beta reply text" }] },
  ],
};

const streamBody = (withWebpage) =>
  [
    "event: start\ndata: {}\n\n",
    ...(withWebpage
      ? [
          `event: webpage\ndata: ${JSON.stringify({ webpageId: "webpage-route", path: "index.html", files: ["index.html"], versionId: null, build: null, previewBase: PREVIEW_BASE, previewUrl: PREVIEW_URL })}\n\n`,
        ]
      : []),
    'event: token\ndata: {"content":"Fresh reply"}\n\n',
    "event: done\ndata: {}\n\n",
  ].join("");

// One mocked app per scenario; `state` lets a scenario change the server.
async function openApp({ viewport = { width: 1536, height: 1024 }, signedIn = true, chats = [chatA, chatB], webpageStream = false } = {}) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  const state = { signedIn, chats: [...chats], deleted: [] };
  page.on(
    "console",
    (m) =>
      m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(m.text()),
  );
  page.on("pageerror", (e) => errors.push(String(e)));
  // A first visit shows the language picker; pick English up front.
  await page.addInitScript(() => {
    try {
      localStorage.setItem("cappuccina-lang", "en");
    } catch {}
  });
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill(
      state.signedIn
        ? json({ id: "check", email: "check@example.com", displayName: "Check" })
        : json({ detail: "Not signed in" }, 401),
    );
  });
  await page.route("**/api/v1/chats", async (route) => {
    await route.fulfill(
      route.request().method() === "GET" ? json(state.chats) : json({ id: "new-1", title: "New chat" }),
    );
  });
  await page.route("**/api/v1/chats/*", async (route) => {
    const method = route.request().method();
    const id = new URL(route.request().url()).pathname.split("/").pop();
    if (method === "DELETE") {
      state.deleted.push(id);
      state.chats = state.chats.filter((c) => c.id !== id);
      await route.fulfill({ status: 204, body: "" });
    } else {
      await route.fulfill(json(state.chats.find((c) => c.id === id) ?? { detail: "Not found" }, 200));
    }
  });
  await page.route("**/api/v1/memories**", (route) => route.fulfill(json([])));
  await page.route("**/api/v1/webpages/**", async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split("/api/v1/webpages/webpage-route/")[1]?.split("/").filter(Boolean) ?? [];
    const [sub, ...rest] = parts;
    if (sub === "preview") {
      await route.fulfill({ status: 200, contentType: "text/html", body: PAGE_HTML });
    } else if (sub === "versions") {
      await route.fulfill(json({ activeVersionId: "v1", versions: [] }));
    } else if (sub === "files") {
      await route.fulfill(json({ path: rest.join("/"), content: PAGE_HTML }));
    } else if (!sub) {
      await route.fulfill(
        json({
          webpageId: "webpage-route",
          files: ["index.html"],
          updatedAt: "2026-01-01T00:00:00Z",
          activeVersionId: "v1",
          previewUrl: PREVIEW_URL,
          previewBase: PREVIEW_BASE,
          entry: "index.html",
          serveRoot: "",
          hasBuild: false,
          lastBuild: null,
        }),
      );
    } else {
      await route.fulfill(json({ detail: "Not found" }, 404));
    }
  });
  await page.route("**/api/v1/chat/stream", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: streamBody(webpageStream) }),
  );
  return { page, state, errors };
}

const pathOf = (page) => new URL(page.url()).pathname;
const searchOf = (page) => new URL(page.url()).search;
const bubbleText = (page) => page.locator(".bubble").allTextContents().then((t) => t.join(" | "));
async function waitUntil(page, fn, timeout = 5000) {
  const start = Date.now();
  for (;;) {
    if (await fn()) return true;
    if (Date.now() - start > timeout) return false;
    await page.waitForTimeout(50);
  }
}
const canvasVisible = (page) => page.locator(".webpage-canvas").isVisible().catch(() => false);
const textVisible = (page, text) => page.getByText(text, { exact: false }).first().isVisible().catch(() => false);

// --- 1. deep link, sidebar switching, history -------------------------------
{
  const { page, errors } = await openApp();
  await page.goto(`${BASE}/c/A`, { waitUntil: "networkidle" });
  check("deep link /c/A shows chat A", await waitUntil(page, async () => (await bubbleText(page)).includes("Alpha reply text")));
  check("deep link keeps /c/A", pathOf(page) === "/c/A", pathOf(page));
  check("tab title includes chat A's title", await waitUntil(page, async () => (await page.title()).includes("Alpha chat")), await page.title());

  await page.click('.chat-list-item:has-text("Beta chat")');
  check("clicking chat B pushes /c/B", await waitUntil(page, async () => pathOf(page) === "/c/B"), pathOf(page));
  check("chat B content shows", await waitUntil(page, async () => (await bubbleText(page)).includes("Beta reply text")));

  await page.goBack();
  check("goBack returns to /c/A", await waitUntil(page, async () => pathOf(page) === "/c/A"), pathOf(page));
  check("goBack shows A's content", await waitUntil(page, async () => (await bubbleText(page)).includes("Alpha reply text")));
  await page.goForward();
  check("goForward returns to /c/B", await waitUntil(page, async () => pathOf(page) === "/c/B"), pathOf(page));
  check("goForward shows B's content", await waitUntil(page, async () => (await bubbleText(page)).includes("Beta reply text")));
  check("no console errors (history)", errors.length === 0, errors);
  await page.close();
}

// --- 2. canvas <-> /page ----------------------------------------------------
{
  const { page, errors } = await openApp();
  await page.goto(`${BASE}/c/A`, { waitUntil: "networkidle" });
  await page.waitForSelector('.ctrl-btn[aria-label="Show the webpage"]', { timeout: 5000 });
  check("canvas closed on /c/A", !(await canvasVisible(page)));
  await page.click('.ctrl-btn[aria-label="Show the webpage"]');
  check("opening the canvas pushes /c/A/page", await waitUntil(page, async () => pathOf(page) === "/c/A/page"), pathOf(page));
  check("canvas visible on /c/A/page", await waitUntil(page, () => canvasVisible(page)));
  await page.goBack();
  check("goBack returns to /c/A", await waitUntil(page, async () => pathOf(page) === "/c/A"), pathOf(page));
  check("goBack hides the canvas", await waitUntil(page, async () => !(await canvasVisible(page))));
  await page.goForward();
  await waitUntil(page, async () => pathOf(page) === "/c/A/page");
  await page.reload({ waitUntil: "networkidle" });
  check("reload keeps /c/A/page", pathOf(page) === "/c/A/page", pathOf(page));
  check("reload on /c/A/page reopens the canvas", await waitUntil(page, () => canvasVisible(page)));
  check("no console errors (canvas)", errors.length === 0, errors);
  await page.close();
}

// --- 3. settings in the URL (desktop) ---------------------------------------
{
  const { page, errors } = await openApp();
  await page.goto(`${BASE}/c/A?settings=memory`, { waitUntil: "networkidle" });
  check("?settings=memory opens Settings", await waitUntil(page, () => page.locator(".settings-veil").isVisible()));
  check(
    "Settings is on Memory",
    await page.locator(".settings-nav-item.active").first().textContent().then((t) => t.includes("Memory")),
  );
  check("path stays /c/A", pathOf(page) === "/c/A", pathOf(page));
  await page.close();

  const second = await openApp();
  await second.page.goto(`${BASE}/c/A`, { waitUntil: "networkidle" });
  await second.page.focus('[aria-label="Account options"]');
  await second.page.click("text=Settings");
  check("opening Settings from the menu adds ?settings", await waitUntil(second.page, async () => searchOf(second.page).includes("settings=")), searchOf(second.page));
  check("dialog is open", await second.page.locator(".settings-veil").isVisible());
  await second.page.click(".settings-nav-item:has-text('Memory')");
  check("changing section updates the URL", await waitUntil(second.page, async () => searchOf(second.page) === "?settings=memory"), searchOf(second.page));
  await second.page.goBack();
  check("goBack closes the dialog", await waitUntil(second.page, async () => !(await second.page.locator(".settings-veil").isVisible().catch(() => false))));
  check("goBack drops ?settings (one back, despite the section change)", searchOf(second.page) === "" && pathOf(second.page) === "/c/A", second.page.url());
  check("no console errors (settings)", errors.length + second.errors.length === 0, [...errors, ...second.errors]);
  await second.page.close();
}

// --- 4. first send from / ---------------------------------------------------
for (const withWebpage of [false, true]) {
  const label = withWebpage ? "with a webpage event" : "plain";
  const { page, errors } = await openApp({ chats: [], webpageStream: withWebpage });
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  check(`[${label}] / is a fresh chat`, pathOf(page) === "/" && (await page.locator(".bubble").count()) === 0);
  const lengthBefore = await page.evaluate(() => history.length);
  await page.fill('textarea[aria-label="Message Cappuccina"]', "hello there");
  await page.keyboard.press("Enter");
  const expected = withWebpage ? "/c/new-1/page" : "/c/new-1";
  check(`[${label}] URL becomes ${expected}`, await waitUntil(page, async () => pathOf(page) === expected), pathOf(page));
  await waitUntil(page, async () => (await bubbleText(page)).includes("Fresh reply"));
  if (withWebpage) {
    check(`[${label}] canvas opens`, await waitUntil(page, () => canvasVisible(page)));
    // /c/new-1 was replaced, then /page pushed on top: exactly one new entry.
    const lengthAfter = await page.evaluate(() => history.length);
    check(`[${label}] only the webpage push adds history`, lengthAfter === lengthBefore + 1, `${lengthBefore} -> ${lengthAfter}`);
  } else {
    const lengthAfter = await page.evaluate(() => history.length);
    check(`[${label}] history.length unchanged (replace, not push)`, lengthAfter === lengthBefore, `${lengthBefore} -> ${lengthAfter}`);
  }
  check(`[${label}] no console errors`, errors.length === 0, errors);
  await page.close();
}

// --- 5. unknown chat and deleting the active chat ---------------------------
{
  const { page, errors } = await openApp();
  await page.goto(`${BASE}/c/does-not-exist`, { waitUntil: "networkidle" });
  check("/c/does-not-exist ends at /", await waitUntil(page, async () => pathOf(page) === "/"), pathOf(page));
  await page.close();

  const del = await openApp();
  await del.page.goto(`${BASE}/c/B`, { waitUntil: "networkidle" });
  await del.page.waitForSelector(".chat-list-item.active");
  await del.page.hover('.chat-list-item:has-text("Beta chat")');
  await del.page.click('[aria-label="Chat options for Beta chat"], .chat-list-item.active .chat-menu-btn');
  await del.page.click('.chat-menu-item.danger');
  await del.page.click(".btn-toss");
  check("deleting the active chat ends at /", await waitUntil(del.page, async () => pathOf(del.page) === "/"), pathOf(del.page));
  check("DELETE was sent for B", del.state.deleted.includes("B"), del.state.deleted);
  check("deleted chat leaves the sidebar", (await del.page.locator('.chat-list-item:has-text("Beta chat")').count()) === 0);
  check("no console errors (unknown/delete)", errors.length + del.errors.length === 0, [...errors, ...del.errors]);
  await del.page.close();
  await page.close().catch(() => {});
}

// --- 6. signed out deep link, then sign in ----------------------------------
{
  const { page, state } = await openApp({ signedIn: false });
  await page.goto(`${BASE}/c/A`, { waitUntil: "networkidle" });
  check("signed out: lands on /", await waitUntil(page, async () => pathOf(page) === "/"), pathOf(page));
  check("signed out: landing page shows", await waitUntil(page, () => page.locator(".landing-title").isVisible().catch(() => false)));
  const saved = await page.evaluate(() => sessionStorage.getItem("cappuccina-return-to"));
  check("signed out: return key saved", Boolean(saved) && JSON.parse(saved).path === "/c/A", saved);
  state.signedIn = true;
  await page.reload({ waitUntil: "networkidle" });
  check("signed in again: reload lands on /c/A", await waitUntil(page, async () => pathOf(page) === "/c/A"), pathOf(page));
  check("signed in again: chat A shows", await waitUntil(page, async () => (await bubbleText(page)).includes("Alpha reply text")));
  check("return key is consumed", (await page.evaluate(() => sessionStorage.getItem("cappuccina-return-to"))) === null);

  // An old entry (an abandoned sign-in trip) must not hijack a later reload.
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.evaluate(() =>
    sessionStorage.setItem("cappuccina-return-to", JSON.stringify({ path: "/c/B", at: Date.now() - 16 * 60 * 1000 })),
  );
  await page.reload({ waitUntil: "networkidle" });
  check("expired return key is ignored", pathOf(page) === "/", pathOf(page));
  check("expired return key is removed", (await page.evaluate(() => sessionStorage.getItem("cappuccina-return-to"))) === null);
  await page.close();
}

// --- 7. mobile settings -----------------------------------------------------
{
  const mobile = { width: 375, height: 812 };
  const dialogOpen = (page) => page.locator(".settings-veil").isVisible().catch(() => false);

  // Opened by deep link: no entry was pushed, so Back leaves the app (the page
  // before it, here about:blank). Documented, not asserted.
  const deep = await openApp({ viewport: mobile });
  await deep.page.goto(`${BASE}/c/A?settings=`, { waitUntil: "networkidle" });
  check("mobile ?settings= shows the index", await waitUntil(deep.page, () => deep.page.locator(".settings-index").isVisible().catch(() => false)));
  await deep.page.click(".settings-index-item >> nth=0");
  check("mobile section tap shows the detail pane", await waitUntil(deep.page, () => deep.page.locator(".settings-pane").isVisible().catch(() => false)));
  console.log("INFO: deep link, section open ->", deep.page.url());
  await deep.page.goBack();
  await deep.page.waitForTimeout(400);
  console.log("INFO: deep link, after goBack ->", deep.page.url(), "(no pushed entry, so Back leaves the app)");
  await deep.page.close();

  // Opened from the UI: one push, section taps replace, so Back closes Settings.
  const { page } = await openApp({ viewport: mobile });
  await page.goto(`${BASE}/c/A`, { waitUntil: "networkidle" });
  await page.click('.top-menu .ctrl-btn');
  await page.click(".top-menu-pop .user-menu-item");
  check("mobile: menu opens Settings on the index", (await waitUntil(page, () => page.locator(".settings-index").isVisible().catch(() => false))) && searchOf(page) === "?settings=", page.url());
  await page.click(".settings-index-item >> nth=0");
  check("mobile: tapping a section shows its detail and URL", (await waitUntil(page, () => page.locator(".settings-pane").isVisible().catch(() => false))) && /\?settings=\w+/.test(searchOf(page)), page.url());
  await page.goBack();
  await page.waitForTimeout(400);
  const open = await dialogOpen(page);
  console.log("INFO: UI-opened, after goBack from a section ->", page.url(), "| dialog open:", open, "| index shown:", await page.locator(".settings-index").isVisible().catch(() => false));
  check("mobile: goBack from a section closes Settings in one step", !open && searchOf(page) === "" && pathOf(page) === "/c/A", page.url());
  await page.close();
}

await browser.close();
console.log(failures ? `\n${failures} check(s) FAILED` : "\nAll checks passed");
if (failures) process.exit(1);
