import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

const INDEX = "<!doctype html><html><head><title>Cat zone</title><link rel=\"stylesheet\" href=\"styles.css\"></head><body><h1>Hello cats</h1><img src=\"cat.png\" alt=\"a cat\"><script src=\"script.js\"></script></body></html>";
const CSS = "body { background: rgb(1, 2, 3); }";
const JS =
  "const heading = 'from script';\nfunction label(text) {\n  return text;\n}\ndocument.querySelector('h1').textContent = label(heading);";

// A real 1x1 PNG: the canvas fetches folder assets itself and inlines them as
// data URLs, so this is what proves the picture reaches the sandboxed iframe.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);

// unauthenticated preview: mock the session + chat list
await page.route("**/api/v1/auth/me", async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ id: "check", email: "check@example.com", displayName: "Check" }),
  });
});
// mutable server state: empty until the stream "persists" a finished turn
let savedChats = [];
await page.route("**/api/v1/chats", async (route) => {
  const body =
    route.request().method() === "GET"
      ? JSON.stringify(savedChats)
      : JSON.stringify({ id: "chat-check", title: "New chat" });
  await route.fulfill({ status: 200, contentType: "application/json", body });
});
// The canvas fetches the environment, the iteration list and any folder
// picture's bytes; all three live behind /api/v1/webpages/.
const ASSET = { path: "cat.png", mime: "image/png", size: PNG.length };
let env = { webpageId: null, files: {}, assets: {}, updatedAt: null };
let versions = [];
await page.route("**/api/v1/webpages/**", async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname.includes("/assets/")) {
    await route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    return;
  }
  const body = url.pathname.endsWith("/versions")
    ? { activeVersionId: versions.at(-1)?.id ?? null, versions }
    : env;
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
});

// One page built as three file writes, each announced with a webpage event,
// then a picture added to the folder (announced without file text).
const files = { "index.html": INDEX, "styles.css": CSS, "script.js": JS };
const sse = [
  "event: start\ndata: {}\n\n",
  ...Object.entries(files).flatMap(([path, content], i) => [
    `event: tool_call\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", display: "Building Webpage", args: { path, bytes: content.length }, label: `write_webpage_file(path='${path}')` })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", content: JSON.stringify({ status: "webpage_file_written", webpage_id: "env-check", path, bytes: content.length, files: Object.keys(files).slice(0, i + 1) }), ok: true })}\n\n`,
    `event: webpage\ndata: ${JSON.stringify({ webpageId: "env-check", path, files: Object.keys(files).slice(0, i + 1), content, versionId: "v-page", asset: false, assets: {} })}\n\n`,
  ]),
  `event: tool_call\ndata: ${JSON.stringify({ id: "t9", name: "add_webpage_asset", display: "Adding to Webpage", args: { path: ASSET.path, image_id: "img-1" }, label: `add_webpage_asset(path='${ASSET.path}')` })}\n\n`,
  `event: tool_result\ndata: ${JSON.stringify({ id: "t9", name: "add_webpage_asset", content: JSON.stringify({ status: "webpage_asset_added", webpage_id: "env-check", version_id: "v-asset", path: ASSET.path, mime: ASSET.mime, bytes: ASSET.size, files: Object.keys(files), assets: [ASSET.path] }), ok: true })}\n\n`,
  `event: webpage\ndata: ${JSON.stringify({ webpageId: "env-check", path: "", files: Object.keys(files), content: "", versionId: "v-asset", asset: true, assets: { [ASSET.path]: { mime: ASSET.mime, size: ASSET.size } } })}\n\n`,
  'event: token\ndata: {"content":"Your page is ready!"}\n\n',
  "event: done\ndata: {}\n\n",
].join("");

await page.route("**/api/v1/chat/stream", async (route) => {
  await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
});

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await page.fill('textarea[aria-label="Message Cappuccina"]', "make me a webpage about cats");
await page.keyboard.press("Enter");

// 1. the canvas opens on its own and shows the preview
await page.waitForSelector(".webpage-canvas", { timeout: 5000 });
const open = await page.locator(".webpage-canvas").isVisible();
console.log("canvas auto-opened:", open);
await page.waitForSelector(".canvas-frame", { timeout: 5000 });

// 2. the iframe is sandboxed without same-origin, so the page can't touch the app
const sandbox = await page.locator(".canvas-frame").getAttribute("sandbox");
console.log("iframe sandbox:", sandbox);
const sandboxed = sandbox === "allow-scripts";
console.log("iframe sandboxed script-only:", sandboxed);

// 3. the preview inlines the linked stylesheet and script by name
const srcdoc = await page.locator(".canvas-frame").getAttribute("srcdoc");
const inlinedCss = srcdoc.includes(CSS);
const inlinedJs = srcdoc.includes(JS);
const externalTagsGone = !srcdoc.includes('href="styles.css"') && !srcdoc.includes('src="script.js"');
console.log("preview inlines css:", inlinedCss);
console.log("preview inlines js:", inlinedJs);
console.log("linked tags rewritten:", externalTagsGone);

// 4. the rendered iframe really ran the inlined code
const frame = page.frameLocator(".canvas-frame");
const renderedHeading = await frame.locator("h1").textContent({ timeout: 3000 });
console.log("inlined script ran:", renderedHeading === "from script");

// 5. the Code tab opens a file menu; picking a file collapses it to the editor
//    with a back button that returns to the menu.
await page.click('.canvas-tabs button:has-text("Code")');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
const rows = await page.locator(".canvas-file-row .canvas-file-name").allTextContents();
console.log("code files:", rows.join(", "));
const listsAll = ["index.html", "script.js", "styles.css"].every((n) => rows.includes(n));
await page.click('.canvas-file-row:has-text("index.html")');
await page.waitForSelector(".canvas-code-head", { timeout: 3000 });
const menuGone = (await page.locator(".canvas-file-row").count()) === 0;
const openLabel = await page.locator(".canvas-code-head .canvas-file-name").textContent();
console.log("file menu collapses on pick:", menuGone && openLabel === "index.html");
const codeText = await page.locator(".canvas-code-body").textContent();
console.log("code tab shows the page:", codeText.includes("Hello cats"));
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
console.log("back returns to the file menu:", await page.locator(".canvas-file-row").count() >= 3);

// 6. the folder picture: listed with its size, inlined into the preview as a
//    data URL, decoded by the sandboxed iframe, and shown (not edited) in the
//    Code tab.
await page.click('.canvas-tabs button:has-text("Code")');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
const assetRow = await page.locator('.canvas-file-row:has-text("cat.png")').count();
const assetChip = await page
  .locator('.canvas-file-row:has-text("cat.png") .canvas-history-meta')
  .textContent();
console.log("folder picture listed with its size:", assetRow === 1 && /B|KB/.test(assetChip ?? ""));
await page.click('.canvas-file-row:has-text("cat.png")');
await page.waitForSelector(".canvas-asset-preview", { timeout: 3000 });
const assetDecoded = await page
  .locator(".canvas-asset-preview")
  .evaluate((el) => el.naturalWidth > 0);
const noEditForAsset = (await page.locator(".canvas-edit-btn").count()) === 0;
console.log("picture shown in the code tab (decoded, not editable):", assetDecoded && noEditForAsset);
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-tabs button:has-text("Preview")');
await page.waitForSelector(".canvas-frame", { timeout: 3000 });
const withAsset = await page.locator(".canvas-frame").getAttribute("srcdoc");
const assetInlined = withAsset.includes("data:image/png;base64,") && !withAsset.includes('src="cat.png"');
console.log("preview inlines the picture as a data URL:", assetInlined);
const frameImage = await frame
  .locator("img")
  .evaluate((el) => ({ w: el.naturalWidth, src: el.getAttribute("src") }));
console.log("sandboxed iframe decoded the picture:", frameImage.w > 0 && frameImage.src.startsWith("data:image/png"));

// 7. the message carries a card to reopen the canvas
const card = await page.locator(".webpage-card").count();
console.log("message webpage card:", card === 1);

// 8. closing hides it; the top-bar toggle appears and reopens it
await page.click('.canvas-icon-btn[aria-label="Close canvas"]');
await page.waitForTimeout(150);
console.log("canvas closed:", (await page.locator(".webpage-canvas").count()) === 0);
await page.click('.ctrl-btn[aria-label="Show the webpage"]');
await page.waitForSelector(".webpage-canvas", { timeout: 3000 });
console.log("canvas reopened:", await page.locator(".webpage-canvas").isVisible());

// 8b. fullscreen: the canvas covers the whole browser window (whatever its
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
const rendersFullscreen = (await page.locator(".canvas-frame").getAttribute("srcdoc")).includes("Hello cats");
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
// The same small header icon as Refresh/Download (34px, in the header row),
// not a banner or an overlay button.
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

// 9. reload: the canvas restores from the fetched environment. The server now
// has the persisted turn (with its small webpage part) and the environment,
// including the folder picture's metadata and its iteration.
env = {
  webpageId: "env-check",
  files,
  assets: { [ASSET.path]: { mime: ASSET.mime, size: ASSET.size } },
  updatedAt: "2026-01-01T00:00:00Z",
  activeVersionId: "v-asset",
};
versions = [
  {
    id: "v-asset",
    seq: 1,
    parentVersionId: null,
    origin: "ai",
    label: "",
    messageUid: "reply-1",
    assets: [ASSET.path],
    summary: { files: 4, added: 4, removed: 0 },
    createdAt: "2026-01-01T00:00:00Z",
  },
];
savedChats = [
  {
    id: "chat-check",
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
          { type: "webpage", webpageId: "env-check", path: "script.js", files: Object.keys(files) },
          { type: "webpage", webpageId: "env-check", files: Object.keys(files), asset: true },
        ],
      },
    ],
  },
];
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector('.ctrl-btn[aria-label="Show the webpage"]', { timeout: 5000 });
await page.click('.ctrl-btn[aria-label="Show the webpage"]');
await page.waitForSelector(".canvas-frame", { timeout: 5000 });
const restored = (await page.locator(".canvas-frame").getAttribute("srcdoc")).includes("Hello cats");
console.log("canvas restored after reload:", restored);
const reloadedImage = await frame.locator("img").evaluate((el) => el.getAttribute("src") ?? "");
console.log("picture still inlined after reload:", reloadedImage.startsWith("data:image/png"));
const iterationLabel = await page.locator(".canvas-history-count").textContent();
console.log("iteration bar shows the run:", iterationLabel === "Iteration 1 of 1", iterationLabel);

// 10. a canvas too narrow for one row (the page has history here, so all three
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
  !inlinedCss ||
  !inlinedJs ||
  !externalTagsGone ||
  renderedHeading !== "from script" ||
  !listsAll ||
  !highlightOk ||
  !assetInlined ||
  !assetDecoded ||
  !noEditForAsset ||
  !reloadedImage.startsWith("data:image/png") ||
  iterationLabel !== "Iteration 1 of 1" ||
  card !== 1 ||
  !restored ||
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
