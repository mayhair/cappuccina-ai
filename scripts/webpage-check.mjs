// The canvas on the containerized workspace API (see backend/app/main.py): the
// preview iframe loads the served preview URL (a sandboxed, session-less
// capability URL — never srcdoc), the Code tab lists the workspace tree and
// fetches one file's text on demand, and a hand edit saves against the
// iteration it rendered.
// Run against `npm run preview` (port 4173): node scripts/webpage-check.mjs
import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

const INDEX =
  "<!doctype html><html><head><title>Cat zone</title><link rel=\"stylesheet\" href=\"styles.css\"></head><body><h1>Hello cats</h1><img src=\"cat.png\" alt=\"a cat\"><script src=\"script.js\"></script></body></html>";
const CSS = "body { background: rgb(1, 2, 3); }";
const JS =
  "const heading = 'from script';\nfunction label(text) {\n  return text;\n}\ndocument.querySelector('h1').textContent = label(heading);";

// A real 1x1 PNG: the preview route serves it like any other workspace file, so
// the sandboxed iframe proves the picture loaded from the site's own URL (the
// canvas holds no bytes of its own).
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);

const CHAT = "chat-check";
// The preview URL is a capability URL scoped to the chat: the canvas iframe and
// the Code tab's <img> both load from it, with no session cookie.
const PREVIEW_BASE = `/api/v1/webpages/${CHAT}/preview/key-check/`;
const PREVIEW_URL = `${PREVIEW_BASE}index.html`;
const TEXT = { "index.html": INDEX, "styles.css": CSS, "script.js": JS };
const BINARY = { "cat.png": { mime: "image/png", size: PNG.length } };
const NAMES = ["index.html", "styles.css", "script.js", "cat.png"];

// The mirrored server state: two AI runs, the second one active.
let activeVersionId = "v-run-2";
const versions = [
  {
    id: "v-run-1",
    seq: 1,
    parentVersionId: null,
    origin: "ai",
    label: "",
    messageUid: "reply-1",
    assets: [],
    summary: { files: 3, added: 3, removed: 0 },
    createdAt: "2026-01-01T00:00:00Z",
  },
  {
    id: "v-run-2",
    seq: 2,
    parentVersionId: "v-run-1",
    origin: "ai",
    label: "",
    messageUid: "reply-2",
    assets: [],
    summary: { files: 1, added: 1, removed: 0 },
    createdAt: "2026-01-01T00:01:00Z",
  },
];
const envJson = () => ({
  webpageId: "webpage-check",
  files: NAMES,
  updatedAt: "2026-01-01T00:01:00Z",
  activeVersionId,
  previewUrl: PREVIEW_URL,
  previewBase: PREVIEW_BASE,
  entry: "index.html",
  serveRoot: "",
  hasBuild: false,
  lastBuild: null,
});
const activationRequests = [];
const saveRequests = [];

const json = (body, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

// unauthenticated preview: mock the session + chat list
await page.route("**/api/v1/auth/me", async (route) => {
  await route.fulfill(
    json({ id: "check", email: "check@example.com", displayName: "Check" }),
  );
});
// mutable server state: empty until the stream "persists" a finished turn
let savedChats = [];
await page.route("**/api/v1/chats", async (route) => {
  await route.fulfill(
    json(route.request().method() === "GET" ? savedChats : { id: CHAT, title: "New chat" }),
  );
});
// Everything the workspace exposes: the env, the tree-by-name, one file's text
// (or its binary metadata), the iteration list, an activation, a save, a diff,
// and the served preview (the iframe and its stylesheet/script/picture).
await page.route("**/api/v1/webpages/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const parts = url.pathname.split(`/api/v1/webpages/${CHAT}/`)[1]?.split("/").filter(Boolean) ?? [];
  const [sub, ...rest] = parts;
  if (sub === "preview") {
    const name = rest.slice(1).join("/") || "index.html";
    if (name === "index.html") {
      await route.fulfill({ status: 200, contentType: "text/html", body: INDEX });
    } else if (name === "styles.css") {
      await route.fulfill({ status: 200, contentType: "text/css", body: CSS });
    } else if (name === "script.js") {
      await route.fulfill({ status: 200, contentType: "text/javascript", body: JS });
    } else if (name === "cat.png") {
      await route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    } else {
      await route.fulfill(json({ detail: "Not found" }, 404));
    }
    return;
  }
  if (sub === "versions" && rest[0]) {
    await route.fulfill(json({ versionId: rest[0], baseVersionId: null, files: [], truncated: false }));
    return;
  }
  if (sub === "versions") {
    await route.fulfill(json({ activeVersionId, versions }));
    return;
  }
  if (sub === "active" && request.method() === "PUT") {
    const body = JSON.parse(request.postData() ?? "{}");
    activationRequests.push(body);
    if (versions.some((v) => v.id === body.versionId)) activeVersionId = body.versionId;
    await route.fulfill(json(envJson()));
    return;
  }
  if (sub === "files" && request.method() === "PUT") {
    const body = JSON.parse(request.postData() ?? "{}");
    saveRequests.push(body);
    await route.fulfill(json(envJson()));
    return;
  }
  if (sub === "files") {
    const path = rest.join("/");
    if (typeof TEXT[path] === "string") {
      await route.fulfill(json({ path, content: TEXT[path] }));
      return;
    }
    if (BINARY[path]) {
      await route.fulfill(json({ path, binary: true, mime: BINARY[path].mime, size: BINARY[path].size }));
      return;
    }
    await route.fulfill(json({ detail: "File not found" }, 404));
    return;
  }
  if (!sub) {
    await route.fulfill(json(envJson()));
    return;
  }
  await route.fulfill(json({ detail: "Not found" }, 404));
});

// One page built as three file writes, each announced with a webpage event,
// then a picture added to the folder. The tools hold no file text any more: the
// canvas refetches the env, the iteration list and the file it shows.
const sse = [
  "event: start\ndata: {}\n\n",
  ...Object.entries(TEXT).flatMap(([path, content], i) => [
    `event: tool_call\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", display: "Building Webpage", args: { path, bytes: content.length }, label: `write_webpage_file(path='${path}')` })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", content: JSON.stringify({ status: "webpage_file_written", webpage_id: "webpage-check", version_id: null, path, bytes: content.length, files: NAMES.slice(0, i + 1) }), ok: true })}\n\n`,
    `event: webpage\ndata: ${JSON.stringify({ webpageId: "webpage-check", path, files: NAMES.slice(0, i + 1), versionId: null, build: null, previewBase: PREVIEW_BASE, previewUrl: PREVIEW_URL })}\n\n`,
  ]),
  `event: tool_call\ndata: ${JSON.stringify({ id: "t9", name: "add_webpage_asset", display: "Adding to Webpage", args: { path: "cat.png", image_id: "img-1" }, label: `add_webpage_asset(path='cat.png')` })}\n\n`,
  `event: tool_result\ndata: ${JSON.stringify({ id: "t9", name: "add_webpage_asset", content: JSON.stringify({ status: "webpage_asset_added", webpage_id: "webpage-check", version_id: null, path: "cat.png", mime: "image/png", bytes: PNG.length, files: NAMES, assets: [] }), ok: true })}\n\n`,
  `event: webpage\ndata: ${JSON.stringify({ webpageId: "webpage-check", path: "", files: NAMES, versionId: null, build: null, previewBase: PREVIEW_BASE, previewUrl: PREVIEW_URL })}\n\n`,
  'event: token\ndata: {"content":"Your page is ready!"}\n\n',
  "event: done\ndata: {}\n\n",
].join("");

await page.route("**/api/v1/chat/stream", async (route) => {
  await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
});

async function waitFor(fn, timeout = 5000) {
  const start = Date.now();
  for (;;) {
    if (await fn()) return true;
    if (Date.now() - start > timeout) return false;
    await page.waitForTimeout(50);
  }
}

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await page.fill('textarea[aria-label="Message Cappuccina"]', "make me a webpage about cats");
await page.keyboard.press("Enter");

// 1. the canvas opens on its own and shows the preview
await page.waitForSelector(".webpage-canvas", { timeout: 5000 });
const open = await page.locator(".webpage-canvas").isVisible();
console.log("canvas auto-opened:", open);
await page.waitForSelector(".canvas-frame", { timeout: 5000 });

// 2. the iframe is sandboxed without same-origin, so the page can't touch the
//    app — and it loads the served preview URL, not an inlined srcdoc.
const sandbox = await page.locator(".canvas-frame").getAttribute("sandbox");
console.log("iframe sandbox:", sandbox);
// Forms and alert/confirm work like an ordinary tab; same-origin never.
const sandboxed =
  sandbox === "allow-scripts allow-forms allow-modals" &&
  !sandbox.includes("allow-same-origin");
console.log("iframe sandboxed without same-origin:", sandboxed);
const frameSrc = await page.locator(".canvas-frame").getAttribute("src");
const srcdoc = await page.locator(".canvas-frame").getAttribute("srcdoc");
console.log("iframe src is the preview URL:", frameSrc === PREVIEW_URL, frameSrc);
console.log("iframe carries no srcdoc:", srcdoc === null);

// 3. the served page really runs in the frame: its linked stylesheet and script
//    load from the preview URL, and the picture too.
const frame = page.frameLocator(".canvas-frame");
const renderedHeading = await frame.locator("h1").textContent({ timeout: 5000 });
console.log("preview ran the served script:", renderedHeading === "from script");
const frameBody = await frame
  .locator("body")
  .evaluate((el) => getComputedStyle(el).backgroundColor);
console.log("preview applied the served stylesheet:", frameBody === "rgb(1, 2, 3)", frameBody);
const frameImage = await frame
  .locator("img")
  .evaluate((el) => ({ w: el.naturalWidth, src: el.src }));
console.log(
  "preview loaded the picture from the preview URL:",
  frameImage.w > 0 && frameImage.src.endsWith("/preview/key-check/cat.png"),
);

// 3b. an error the page reports (the backend's preview runtime posts it from
//     inside the frame) shows as a badge, and "Ask Cappuccina to fix" puts it
//     in the composer for the user to send.
const previewFrame = page.frames().find((f) => f.url().includes(PREVIEW_BASE));
await previewFrame.evaluate(() =>
  parent.postMessage(
    {
      type: "cappuccina-preview-error",
      kind: "error",
      message: "TypeError: Cannot read properties of null (reading 'hidden')",
      source: "script.js",
      line: 4,
    },
    "*",
  ),
);
// A message from anything but the preview frame is ignored.
await page.evaluate(() =>
  window.postMessage({ type: "cappuccina-preview-error", message: "spoofed" }, "*"),
);
const badge = await page
  .locator(".canvas-page-error")
  .textContent({ timeout: 3000 })
  .catch(() => "");
const badgeOk = badge.includes("(script.js:4)") && !badge.includes("spoofed");
console.log("page error shows as a badge:", badgeOk, badge);
await page.click(".canvas-page-error-fix");
const composer = await page.locator('textarea[aria-label="Message Cappuccina"]').inputValue();
const askedFix =
  composer.includes("Cannot read properties of null") &&
  (await page.locator(".canvas-page-error").count()) === 0;
console.log("ask-to-fix fills the composer:", askedFix);
await page.locator('textarea[aria-label="Message Cappuccina"]').fill("");

// 4. the Code tab lists the workspace tree (entry first), fetches a file's text
//    on demand, and highlights it.
await page.click('.canvas-tabs button:has-text("Code")');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
const rows = await page.locator(".canvas-file-row .canvas-file-name").allTextContents();
console.log("code files:", rows.join(", "));
const listsAll = NAMES.every((n) => rows.includes(n));
console.log("code tab lists the whole tree (entry first):", listsAll && rows[0] === NAMES[0]);
await page.click('.canvas-file-row:has-text("index.html")');
await page.waitForSelector(".canvas-code-head", { timeout: 3000 });
const menuGone = (await page.locator(".canvas-file-row").count()) === 0;
const openLabel = await page.locator(".canvas-code-head .canvas-file-name").textContent();
console.log("file menu collapses on pick:", menuGone && openLabel === "index.html");
await page.waitForSelector(".canvas-code-body .token.tag", { timeout: 3000 });
const codeText = await page.locator(".canvas-code-body").textContent();
console.log("code tab shows the fetched file:", codeText.includes("Hello cats"));
const htmlTag = await page.locator(".canvas-code-body .token.tag").first().textContent();
console.log("html highlighted (tag token):", !!htmlTag);
const htmlAttr = await page.locator(".canvas-code-body .token.attr-name").count();
console.log("html attributes highlighted:", htmlAttr > 0);
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-file-row:has-text("styles.css")');
await page.waitForSelector(".canvas-code-body .token.selector", { timeout: 3000 });
const cssSelector = await page.locator(".canvas-code-body .token.selector").count();
console.log("css highlighted (selector token):", cssSelector > 0);
const cssProperty = await page.locator(".canvas-code-body .token.property").count();
console.log("css properties highlighted:", cssProperty > 0);
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-file-row:has-text("script.js")');
await page.waitForSelector(".canvas-code-body .token.keyword", { timeout: 3000 });
const jsKeyword = await page.locator(".canvas-code-body .token.keyword").count();
const jsParams = await page.locator(".canvas-code-body .token.parameter").count();
const jsTitle = await page.locator(".canvas-code-body .token.function").count();
console.log("js highlighted (keyword token):", jsKeyword > 0);
console.log("js function/params highlighted:", jsParams > 0 && jsTitle > 0);
const highlightOk =
  !!htmlTag &&
  htmlAttr > 0 &&
  cssSelector > 0 &&
  cssProperty > 0 &&
  jsKeyword > 0 &&
  jsParams > 0 &&
  jsTitle > 0;
const scrollbarStyled = await page
  .locator(".canvas-code-body")
  .evaluate((el) => getComputedStyle(el).scrollbarColor || getComputedStyle(el).scrollbarWidth);
console.log("code pane scrollbar themed:", String(scrollbarStyled).includes("rgba(234, 114, 178") || String(scrollbarStyled) === "thin");
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
console.log("back returns to the file menu:", (await page.locator(".canvas-file-row").count()) >= 4);

// 5. editing + Save puts the file back with the iteration it rendered as the
//    base, and the canvas refetches the env it saved against.
await page.click('.canvas-file-row:has-text("index.html")');
await page.waitForSelector(".canvas-code-head", { timeout: 3000 });
const baseBeforeEdit = activeVersionId;
await page.click('.canvas-icon-btn[aria-label="Edit index.html"]');
await page.waitForSelector("textarea.canvas-code-edit", { timeout: 3000 });
await page.fill("textarea.canvas-code-edit", INDEX.replace("Hello cats", "Hello edited cats"));
await page.click(".canvas-edit-save");
const saved = await waitFor(() => saveRequests.length > 0);
console.log("save issues the file PUT:", saved);
console.log("save carries the rendered baseVersionId:", saveRequests[0]?.baseVersionId === baseBeforeEdit, saveRequests[0]?.baseVersionId);
console.log("save carries the edited text:", (saveRequests[0]?.content ?? "").includes("Hello edited cats"));
await page.waitForTimeout(150);
console.log("editor closes after a successful save:", (await page.locator("textarea.canvas-code-edit").count()) === 0);

// 6. the folder picture: listed in the tree, served by URL into the canvas
//    (decoded, not editable) and into the sandboxed iframe.
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
const assetRow = await page.locator('.canvas-file-row:has-text("cat.png")').count();
console.log("folder picture listed in the tree:", assetRow === 1);
await page.click('.canvas-file-row:has-text("cat.png")');
await page.waitForSelector(".canvas-asset-preview", { timeout: 3000 });
const assetDecoded = await waitFor(() =>
  page.locator(".canvas-asset-preview").evaluate((el) => el.naturalWidth > 0),
);
const assetHint = await page.locator(".canvas-asset").textContent();
const noEditForAsset = (await page.locator(".canvas-edit-btn").count()) === 0;
console.log("picture shown in the code tab (decoded, not editable):", assetDecoded && noEditForAsset);
console.log("picture pane names its type and size:", /image\/png/.test(assetHint) && /B|KB/.test(assetHint), assetHint);
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-tabs button:has-text("Preview")');
await page.waitForSelector(".canvas-frame", { timeout: 3000 });

// 7. the history bar steps through the runs (each step activates server-side)
const bar = () => page.locator(".canvas-history-count").textContent();
console.log("iteration bar shows the latest run:", await waitFor(async () => (await bar()) === "Iteration 2 of 2"), await bar());
await page.click('.canvas-history-step[aria-label="Previous iteration"]');
const steppedBack = await waitFor(() => activationRequests.length > 0);
console.log("previous iteration activates the older run:", steppedBack);
console.log("activation body carries versionId:", activationRequests[0]?.versionId === "v-run-1");
console.log("bar back to iteration 1 of 2:", await waitFor(async () => (await bar()) === "Iteration 1 of 2"), await bar());
await page.click('.canvas-history-step[aria-label="Next iteration"]');
console.log("next iteration returns to 2 of 2:", await waitFor(async () => (await bar()) === "Iteration 2 of 2"), await bar());

// 8. the open-in-a-new-tab button replaces the old download link
const newTabBtn = page.locator('.canvas-icon-btn[aria-label="Open the page in a new tab"]');
const newTabButton = (await newTabBtn.count()) === 1;
console.log("open-in-new-tab button present:", newTabButton);

// 9. the message carries a card to reopen the canvas
const card = await page.locator(".webpage-card").count();
console.log("message webpage card:", card === 1);

// 10. closing hides it; the top-bar toggle appears and reopens it
await page.click('.canvas-icon-btn[aria-label="Close canvas"]');
await page.waitForTimeout(150);
console.log("canvas closed:", (await page.locator(".webpage-canvas").count()) === 0);
await page.click('.ctrl-btn[aria-label="Show the webpage"]');
await page.waitForSelector(".webpage-canvas", { timeout: 3000 });
console.log("canvas reopened:", await page.locator(".webpage-canvas").isVisible());

// 11. fullscreen: the canvas covers the whole browser window (whatever its
// size), the small header icon leaves it, and Escape does too.
const viewport = page.viewportSize();
await page.click('.canvas-icon-btn[aria-label="Fullscreen"]');
await page.waitForTimeout(250);
const fills = await page.evaluate(() => {
  const box = document.querySelector(".webpage-canvas").getBoundingClientRect();
  return {
    x: Math.round(box.x),
    y: Math.round(box.y),
    w: Math.round(box.width),
    h: Math.round(box.height),
  };
});
const fillsWindow =
  fills.x === 0 && fills.y === 0 && fills.w === viewport.width && fills.h === viewport.height;
console.log("canvas fills the window:", fillsWindow, fills, viewport);
// The chat sits directly behind the fullscreen canvas: the surface must be
// opaque (glass would let the conversation read through the panel).
const surface = await page.locator(".webpage-canvas").evaluate((el) => {
  const style = getComputedStyle(el);
  return { background: style.backgroundColor, backdrop: style.backdropFilter };
});
const opaqueSurface =
  /^rgb\(/.test(surface.background) && (surface.backdrop === "none" || !surface.backdrop);
console.log("fullscreen surface is opaque:", opaqueSurface, surface);
const rendersFullscreen =
  (await page.locator(".canvas-frame").getAttribute("src")) === PREVIEW_URL;
console.log("page still renders fullscreen:", rendersFullscreen);
// With room to spare the tabs stay left of the icons, on one row.
const wideRow = await page.evaluate(() => {
  const t = document.querySelector(".canvas-tabs").getBoundingClientRect();
  const i = document.querySelector(".canvas-icons").getBoundingClientRect();
  return Math.abs(t.y - i.y) < 2 && t.right <= i.x + 1;
});
console.log("wide header keeps tabs then icons on one row:", wideRow);
const exitBtn = page.locator('.canvas-head .canvas-icon-btn[aria-label="Exit fullscreen"]');
const exitBox = await exitBtn.boundingBox();
// The same small header icon as Refresh/Open-in-new-tab (34px, in the header
// row), not a banner or an overlay button.
const exitSmall =
  (await exitBtn.isVisible()) && exitBox.width <= 36 && exitBox.height <= 36 && exitBox.y < 60;
console.log("exit button is a small header icon:", exitSmall, exitBox);
await page.keyboard.press("Escape");
await page.waitForTimeout(250);
const backBox = await page.locator(".webpage-canvas").boundingBox();
const escapeExits = backBox.width < viewport.width - 20 && backBox.x > 1;
console.log("escape leaves fullscreen:", escapeExits);
await page.setViewportSize({ width: 900, height: 620 });
await page.click('.canvas-icon-btn[aria-label="Fullscreen"]');
await page.waitForTimeout(250);
const smallBox = await page.locator(".webpage-canvas").boundingBox();
const fillsSmall =
  Math.abs(smallBox.x) < 1 && Math.abs(smallBox.y) < 1 && Math.abs(smallBox.width - 900) < 1 && Math.abs(smallBox.height - 620) < 1;
console.log("fills a smaller window too:", fillsSmall, smallBox);
await page.click('.canvas-icon-btn[aria-label="Exit fullscreen"]');
await page.setViewportSize({ width: viewport.width, height: viewport.height });
await page.waitForTimeout(250);
const restoresPanel = (await page.locator(".webpage-canvas").boundingBox()).width < viewport.width - 20;
console.log("exit button restores the panel:", restoresPanel);

// 12. reload: the canvas restores from the fetched environment. The server now
// has the persisted turn (with its small webpage part) and the workspace env,
// including its preview URL and its iterations.
savedChats = [
  {
    id: CHAT,
    title: "New chat",
    mode: "chat",
    subject: null,
    messages: [
      { id: "u1", role: "user", content: "make me a webpage about cats" },
      {
        id: "a1",
        parentId: "u1",
        role: "assistant",
        content: "Your page is ready!",
        parts: [
          { type: "text", text: "Your page is ready!" },
          { type: "webpage", webpageId: "webpage-check", path: "script.js", files: NAMES },
        ],
      },
    ],
  },
];
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector('.ctrl-btn[aria-label="Show the webpage"]', { timeout: 5000 });
await page.click('.ctrl-btn[aria-label="Show the webpage"]');
await page.waitForSelector(".canvas-frame", { timeout: 5000 });
const restoredSrc = await page.locator(".canvas-frame").getAttribute("src");
const reloadedFrame = page.frameLocator(".canvas-frame");
const restored = await waitFor(async () =>
  (await reloadedFrame.locator("h1").textContent().catch(() => null)) === "from script",
);
console.log("canvas restored after reload:", restored && restoredSrc === PREVIEW_URL);
const reloadedImage = await reloadedFrame
  .locator("img")
  .evaluate((el) => el.naturalWidth > 0 && el.src.endsWith("/preview/key-check/cat.png"));
console.log("picture still loaded after reload:", reloadedImage);
const iterationLabel = await page.locator(".canvas-history-count").textContent();
console.log("iteration bar shows the run:", iterationLabel === "Iteration 2 of 2", iterationLabel);

// 13. a canvas too narrow for one row (the page has history here, so all three
// tabs show): the icons keep the top row and the tabs drop below them — never
// an icon orphaned under the tabs — and nothing is clipped.
await page.click('.canvas-icon-btn[aria-label="Fullscreen"]');
await page.setViewportSize({ width: 360, height: 640 });
await page.waitForTimeout(250);
const narrow = await page.evaluate(() => {
  const box = (sel) => {
    const r = document.querySelector(sel).getBoundingClientRect();
    return { x: r.x, y: r.y, right: r.right, bottom: r.bottom };
  };
  const head = document.querySelector(".canvas-head");
  return {
    tabs: box(".canvas-tabs"),
    icons: box(".canvas-icons"),
    buttons: [...document.querySelectorAll(".canvas-icons .canvas-icon-btn")].map((b) => {
      const r = b.getBoundingClientRect();
      return { x: r.x, right: r.right, bottom: r.bottom };
    }),
    width: document.querySelector(".webpage-canvas").getBoundingClientRect().width,
    clipped: head.scrollWidth > head.clientWidth,
  };
});
const iconsAboveTabs = narrow.icons.bottom <= narrow.tabs.y + 1;
const narrowFits =
  !narrow.clipped &&
  [...narrow.buttons, narrow.tabs].every((b) => b.x >= 0 && b.right <= narrow.width + 1);
console.log(
  "narrow header stacks the icons above the tabs:",
  iconsAboveTabs,
  `icons.bottom=${Math.round(narrow.icons.bottom)} tabs.y=${Math.round(narrow.tabs.y)}`,
);
console.log("narrow header fits, nothing clipped:", narrowFits);
await page.click('.canvas-icon-btn[aria-label="Exit fullscreen"]');
await page.setViewportSize({ width: viewport.width, height: viewport.height });
await page.waitForTimeout(250);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();

if (
  !open ||
  !sandboxed ||
  !badgeOk ||
  !askedFix ||
  frameSrc !== PREVIEW_URL ||
  srcdoc !== null ||
  renderedHeading !== "from script" ||
  frameBody !== "rgb(1, 2, 3)" ||
  frameImage.w === 0 ||
  !frameImage.src.endsWith("/preview/key-check/cat.png") ||
  !listsAll ||
  !menuGone ||
  !highlightOk ||
  !saved ||
  saveRequests[0]?.baseVersionId !== baseBeforeEdit ||
  !assetDecoded ||
  !noEditForAsset ||
  !newTabButton ||
  card !== 1 ||
  !restored ||
  restoredSrc !== PREVIEW_URL ||
  !reloadedImage ||
  iterationLabel !== "Iteration 2 of 2" ||
  !fillsWindow ||
  !opaqueSurface ||
  !rendersFullscreen ||
  !exitSmall ||
  !escapeExits ||
  !fillsSmall ||
  !restoresPanel ||
  !wideRow ||
  !iconsAboveTabs ||
  !narrowFits
) {
  process.exit(1);
}
