import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

const INDEX = "<!doctype html><html><head><title>Cat zone</title><link rel=\"stylesheet\" href=\"styles.css\"></head><body><h1>Hello cats</h1><script src=\"script.js\"></script></body></html>";
const CSS = "body { background: rgb(1, 2, 3); }";
const JS =
  "const heading = 'from script';\nfunction label(text) {\n  return text;\n}\ndocument.querySelector('h1').textContent = label(heading);";

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
// The canvas fetches the environment; start empty, then serve the built page.
let env = { webpageId: null, files: {}, updatedAt: null };
await page.route("**/api/v1/webpages/**", async (route) => {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(env) });
});

// One page built as three file writes, each announced with a webpage event.
const files = { "index.html": INDEX, "styles.css": CSS, "script.js": JS };
const sse = [
  "event: start\ndata: {}\n\n",
  ...Object.entries(files).flatMap(([path, content], i) => [
    `event: tool_call\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", display: "Building Webpage", args: { path, bytes: content.length }, label: `write_webpage_file(path='${path}')` })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", content: JSON.stringify({ status: "webpage_file_written", webpage_id: "env-check", path, bytes: content.length, files: Object.keys(files).slice(0, i + 1) }), ok: true })}\n\n`,
    `event: webpage\ndata: ${JSON.stringify({ webpageId: "env-check", path, files: Object.keys(files).slice(0, i + 1), content })}\n\n`,
  ]),
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
const htmlTag = await page.locator(".canvas-code-body .hljs-name").first().textContent();
console.log("html highlighted (tag token):", !!htmlTag);
const htmlAttr = await page.locator(".canvas-code-body .hljs-attr").count();
console.log("html attributes highlighted:", htmlAttr > 0);
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-file-row:has-text("styles.css")');
await page.waitForSelector(".canvas-code-body .hljs-selector-tag", { timeout: 3000 });
const cssSelector = await page.locator(".canvas-code-body .hljs-selector-tag").count();
console.log("css highlighted (selector token):", cssSelector > 0);
const cssProperty = await page.locator(".canvas-code-body .hljs-attribute").count();
console.log("css properties highlighted:", cssProperty > 0);
await page.click('.canvas-back-btn');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-file-row:has-text("script.js")');
await page.waitForSelector(".canvas-code-body .hljs-keyword", { timeout: 3000 });
const jsKeyword = await page.locator(".canvas-code-body .hljs-keyword").count();
const jsParams = await page.locator(".canvas-code-body .hljs-params").count();
const jsTitle = await page.locator(".canvas-code-body .hljs-title").count();
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

// 6. the message carries a card to reopen the canvas
const card = await page.locator(".webpage-card").count();
console.log("message webpage card:", card === 1);

// 7. closing hides it; the top-bar toggle appears and reopens it
await page.click('.canvas-icon-btn[aria-label="Close canvas"]');
await page.waitForTimeout(150);
console.log("canvas closed:", (await page.locator(".webpage-canvas").count()) === 0);
await page.click('.ctrl-btn[aria-label="Show the webpage"]');
await page.waitForSelector(".webpage-canvas", { timeout: 3000 });
console.log("canvas reopened:", await page.locator(".webpage-canvas").isVisible());

// 8. reload: the canvas restores from the fetched environment. The server now
// has the persisted turn (with its small webpage part) and the environment.
env = { webpageId: "env-check", files, updatedAt: "2026-01-01T00:00:00Z" };
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
  card !== 1 ||
  !restored
) {
  process.exit(1);
}
