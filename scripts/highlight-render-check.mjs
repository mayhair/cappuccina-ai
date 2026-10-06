// Render-level validation for the Prism integration. Covers every
// surface the migration touched: the webpage canvas Code pane (light + dark),
// the chat tool-args JSON panel, and markdown fenced code blocks. Also proves
// the canvas escapes hostile source instead of injecting it.
// Run against `npm run preview` (port 4173): node scripts/highlight-render-check.mjs
import { chromium } from "playwright";

const BASE = process.env.PREVIEW_URL || "http://localhost:4173";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

const failures = [];
const check = (label, condition) => {
  if (!condition) failures.push(label);
  console.log(`${condition ? "ok  " : "FAIL"} ${label}`);
};

// Prism token class -> palette variable it must resolve to, mirroring src/styles.css.
const TOKEN_WIRING = {
  "token comment": "--code-comment",
  "token prolog": "--code-comment",
  "token cdata": "--code-comment",
  "token doctype": "--code-comment",
  "token hashbang": "--code-comment",
  "token keyword": "--code-keyword",
  "token doctype-tag": "--code-keyword",
  "token atrule": "--code-keyword",
  "token rule": "--code-keyword",
  "token important": "--code-keyword",
  "token null": "--code-keyword",
  "token tag": "--code-tag",
  "token selector": "--code-tag",
  "token namespace": "--code-tag",
  "token name": "--code-tag",
  "token selector-function-argument": "--code-tag",
  "token attr-name": "--code-attr",
  "token property": "--code-attr",
  "token literal-property": "--code-attr",
  "token string-property": "--code-attr",
  "token attr-value": "--code-string",
  "token string": "--code-string",
  "token url": "--code-string",
  "token regex": "--code-string",
  "token regex-source": "--code-string",
  "token regex-delimiter": "--code-string",
  "token regex-flags": "--code-string",
  "token language-regex": "--code-string",
  "token template-string": "--code-string",
  "token template-punctuation": "--code-string",
  "token number": "--code-number",
  "token boolean": "--code-literal",
  "token constant": "--code-literal",
  "token entity": "--code-literal",
  "token named-entity": "--code-literal",
  "token function": "--code-function",
  "token function-variable": "--code-function",
  "token class-name": "--code-function",
  "token parameter": "--code-param",
  "token interpolation": "--code-param",
  "token operator": "--code-punct",
  "token punctuation": "--code-punct",
  "token attr-equals": "--code-punct",
  "token interpolation-punctuation": "--code-punct",
};

// Proves the stylesheet rules actually apply: each token class's computed color
// must equal the computed color of the palette variable it should use.
async function checkWiring(label) {
  const result = await page.evaluate((mapping) => {
    const probe = document.createElement("div");
    document.body.appendChild(probe);
    const colorOf = (apply) => {
      const s = document.createElement("span");
      apply(s);
      s.textContent = "x";
      probe.appendChild(s);
      const c = getComputedStyle(s).color;
      s.remove();
      return c;
    };
    const tokens = {};
    const vars = {};
    for (const [cls, name] of Object.entries(mapping)) {
      tokens[cls] = colorOf((s) => (s.className = cls));
      if (!(name in vars)) vars[name] = colorOf((s) => (s.style.color = `var(${name})`));
    }
    probe.remove();
    return { tokens, vars };
  }, TOKEN_WIRING);
  const bad = Object.entries(TOKEN_WIRING).filter(([cls, name]) => result.tokens[cls] !== result.vars[name]);
  check(`${label}: all ${Object.keys(TOKEN_WIRING).length} token classes resolve to their palette variable`, bad.length === 0);
  if (bad.length) console.log("  mismatched:", bad.map(([c, n]) => `${c} != ${n}`).join(", "));
}

const INDEX =
  '<!doctype html><html><head><link rel="stylesheet" href="styles.css"></head><body><h1>Hello cats</h1><script src="script.js"></script></body></html>';
const CSS = "body { color: rgb(1, 2, 3); }";
// Hostile string literal: if the canvas failed to escape, this would become a
// real <img> in the app DOM and fire onerror.
const JS = 'const evil = "<img src=x onerror=window.__code_xss=1>";\nfunction f(x) { return x; }';
// The workspace tree the canvas lists and fetches one file of at a time.
const TEXT = { "index.html": INDEX, "styles.css": CSS, "script.js": JS };

await page.route("**/api/v1/auth/me", (route) =>
  route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ id: "check", email: "check@example.com", displayName: "Check" }),
  }),
);

let savedChats = [];
await page.route("**/api/v1/chats", (route) =>
  route.fulfill({
    status: 200,
    contentType: "application/json",
    body:
      route.request().method() === "GET"
        ? JSON.stringify(savedChats)
        : JSON.stringify({ id: "chat-check", title: "New chat" }),
  }),
);

// The canvas fetches the env (names only), one file's text at a time, and the
// served preview URL; the sandboxed iframe needs a real document to load.
const NAMES = Object.keys(TEXT);
const PREVIEW_BASE = `/api/v1/webpages/chat-check/preview/key-check/`;
let env = {
  webpageId: "webpage-check",
  files: NAMES,
  updatedAt: "2026-01-01T00:00:00Z",
  activeVersionId: null,
  previewUrl: `${PREVIEW_BASE}index.html`,
  previewBase: PREVIEW_BASE,
  entry: "index.html",
  serveRoot: "",
  hasBuild: false,
  lastBuild: null,
};
await page.route("**/api/v1/webpages/**", (route) => {
  const url = new URL(route.request().url());
  const parts = url.pathname.split("/api/v1/webpages/")[1]?.split("/").filter(Boolean) ?? [];
  const [, sub, ...rest] = parts;
  if (sub === "preview") {
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>mock</h1></body></html>",
    });
  }
  if (sub === "files") {
    const path = rest.join("/");
    return typeof TEXT[path] === "string"
      ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ path, content: TEXT[path] }) })
      : route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "File not found" }) });
  }
  if (sub === "versions") {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ activeVersionId: env.activeVersionId, versions: [] }),
    });
  }
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(env) });
});

const sse = [
  "event: start\ndata: {}\n\n",
  ...Object.entries(TEXT).flatMap(([path, content], i) => [
    `event: tool_call\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", display: "Building Webpage", args: { path, bytes: content.length } })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", content: JSON.stringify({ status: "webpage_file_written", webpage_id: "webpage-check", version_id: null, path, bytes: content.length, files: NAMES.slice(0, i + 1) }), ok: true })}\n\n`,
    `event: webpage\ndata: ${JSON.stringify({ webpageId: "webpage-check", path, files: NAMES.slice(0, i + 1), versionId: null, build: null, previewBase: PREVIEW_BASE, previewUrl: `${PREVIEW_BASE}index.html` })}\n\n`,
  ]),
  'event: token\ndata: {"content":"Done!"}\n\n',
  "event: done\ndata: {}\n\n",
].join("");

await page.route("**/api/v1/chat/stream", (route) =>
  route.fulfill({ status: 200, contentType: "text/event-stream", body: sse }),
);

await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.fill('textarea[aria-label="Message Cappuccina"]', "make me a webpage");
await page.keyboard.press("Enter");
await page.waitForSelector(".webpage-canvas", { timeout: 5000 });
await page.waitForSelector(".webpage-card", { timeout: 5000 });

// 1. tool-args JSON panel uses Prism tokens. Tool calls sit in a Thinking block
//    that auto-collapses when the stream finishes, so open it first.
await page.locator(".thinking-head").first().click();
await page.waitForSelector(".tool-call-head", { timeout: 3000 });
await page.locator(".tool-call-head").first().click();
await page.waitForSelector(".tool-args", { timeout: 3000 });
const argKeys = await page.locator(".tool-args .token.property").count();
const argStrings = await page.locator(".tool-args .token.string").count();
check("tool-args: JSON keys highlighted", argKeys > 0);
check("tool-args: JSON strings highlighted", argStrings > 0);

// 2. canvas Code pane: JS tokens, escaping, and theme colors.
await page.click('.canvas-tabs button:has-text("Code")');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-file-row:has-text("script.js")');
await page.waitForSelector(".canvas-code-body .token.keyword", { timeout: 3000 });

check("canvas js: keyword token", (await page.locator(".canvas-code-body .token.keyword").count()) > 0);
check("canvas js: function title token", (await page.locator(".canvas-code-body .token.function").count()) > 0);
check("canvas js: parameter token", (await page.locator(".canvas-code-body .token.parameter").count()) > 0);
const keywordWeight = await page
  .locator(".canvas-code-body .token.keyword")
  .first()
  .evaluate((el) => getComputedStyle(el).fontWeight);
check("canvas js: keyword weight survives the token reset", keywordWeight === "600");
check("canvas escaping: no injected <img>", (await page.locator(".canvas-code-body img").count()) === 0);
const xssFired = await page.evaluate(() => window.__code_xss);
check("canvas escaping: onerror never ran", xssFired === undefined);
const codeText = await page.locator(".canvas-code-body").textContent();
check("canvas escaping: hostile source shown as text", codeText.includes("<img src=x onerror="));

await page.evaluate(() => {
  document.documentElement.dataset.theme = "light";
});
await checkWiring("css light theme");
{
  // The requested distinction: a JS variable (plain text = the surface colour) must
  // not match the operators/punctuation around it.
  const theme = "light";
  const identColor = await page
    .locator(".canvas-code-body")
    .evaluate((el) => getComputedStyle(el).color);
  const operatorColor = await page
    .locator(".canvas-code-body .token.operator")
    .first()
    .evaluate((el) => getComputedStyle(el).color);
  const { ident, punct } = await page.evaluate(() => {
    const probe = document.createElement("span");
    document.body.appendChild(probe);
    const varColor = (name) => {
      probe.style.color = `var(${name})`;
      return getComputedStyle(probe).color;
    };
    const out = { ident: varColor("--code-ident"), punct: varColor("--code-punct") };
    probe.remove();
    return out;
  });
  check(`${theme} canvas: variable text uses --code-ident`, identColor === ident);
  check(`${theme} canvas: variable color differs from operator/punctuation color`, identColor !== operatorColor);
  check(`${theme} canvas: operator color uses --code-punct`, operatorColor === punct);
}
const lightColor = await page
  .locator(".canvas-code-body .token.keyword")
  .first()
  .evaluate((el) => getComputedStyle(el).color);
await page.evaluate(() => {
  document.documentElement.dataset.theme = "dark";
});
await checkWiring("css dark theme");
{
  // Same distinction as the light theme: identifiers get their own colour.
  const theme = "dark";
  const identColor = await page
    .locator(".canvas-code-body")
    .evaluate((el) => getComputedStyle(el).color);
  const operatorColor = await page
    .locator(".canvas-code-body .token.operator")
    .first()
    .evaluate((el) => getComputedStyle(el).color);
  const { ident, punct } = await page.evaluate(() => {
    const probe = document.createElement("span");
    document.body.appendChild(probe);
    const varColor = (name) => {
      probe.style.color = `var(${name})`;
      return getComputedStyle(probe).color;
    };
    const out = { ident: varColor("--code-ident"), punct: varColor("--code-punct") };
    probe.remove();
    return out;
  });
  check(`${theme} canvas: variable text uses --code-ident`, identColor === ident);
  check(`${theme} canvas: variable color differs from operator/punctuation color`, identColor !== operatorColor);
  check(`${theme} canvas: operator color uses --code-punct`, operatorColor === punct);
}
const darkColor = await page
  .locator(".canvas-code-body .token.keyword")
  .first()
  .evaluate((el) => getComputedStyle(el).color);
check("canvas: keyword has a light-theme color", /^rgb/.test(lightColor));
check("canvas: keyword has a dark-theme color", /^rgb/.test(darkColor));
check("canvas: light and dark keyword colors differ", lightColor !== darkColor);
check("canvas: dark theme applied", (await page.evaluate(() => document.documentElement.dataset.theme)) === "dark");

// 2b. Prism's class names are unprefixed, so app rules with the same bare name
//     used to leak into code: `.token.tag` picked up the chat chip's pink pill,
//     uppercase and 0.68em size. Code tokens must stay plain, case-preserving text.
await page.click(".canvas-back-btn");
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-file-row:has-text("index.html")');
await page.waitForSelector(".canvas-code-body .token.tag", { timeout: 3000 });
const tagStyle = await page.locator(".canvas-code-body .token.tag").first().evaluate((el) => {
  const s = getComputedStyle(el);
  return {
    padding: s.padding,
    background: s.backgroundColor,
    radius: s.borderRadius,
    transform: s.textTransform,
    fontFamily: s.fontFamily,
    text: el.textContent,
  };
});
check(
  "canvas html: tag token has no chip styling",
  tagStyle.padding === "0px" &&
    tagStyle.background === "rgba(0, 0, 0, 0)" &&
    tagStyle.radius === "0px" &&
    tagStyle.transform === "none" &&
    /mono/i.test(tagStyle.fontFamily),
);
check("canvas html: tag text keeps source case", tagStyle.text.includes("html") && !tagStyle.text.includes("HTML"));

// 3. markdown fenced block uses Prism tokens (restored chat with a fence).
const fenced = "Here you go:\n\n```js\nconst x = 1;\n```\n";
env = {
  ...env,
  updatedAt: "2026-01-01T00:00:00Z",
  activeVersionId: null,
};
savedChats = [
  {
    id: "chat-check",
    title: "New chat",
    mode: "chat",
    subject: null,
    messages: [
      { id: "u1", role: "user", content: "make me a webpage" },
      {
        id: "a1",
        parentId: "u1",
        role: "assistant",
        content: fenced,
        parts: [{ type: "text", text: fenced }],
      },
    ],
  },
];
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector(".md-pre", { timeout: 5000 });
check("markdown: fenced block rendered", (await page.locator(".md-pre").count()) > 0);
check("markdown: fenced code shows source", (await page.locator(".md-pre").textContent()).includes("const x = 1"));
check("markdown: fenced js keyword token", (await page.locator(".md-pre .token.keyword").count()) > 0);

check("no console errors", errors.length === 0);
if (errors.length) console.log("console errors:", errors);

await browser.close();

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("\nall render checks passed");
