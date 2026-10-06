// Canvas iteration history on the containerized workspace API (see
// backend/app/main.py): stepping through the runs (each step activates
// server-side), the Changes tab, and a hand edit landing as its own iteration
// against the one the tab rendered.
// Run against `npm run preview` (port 4173): node scripts/webpage-versions-check.mjs
import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => {
  if (m.type() !== "error") return;
  if (expectConflict && /status of 409/.test(m.text())) return;
  errors.push(m.text());
});
page.on("pageerror", (e) => errors.push(String(e)));

const failures = [];
// The stale-base probe deliberately provokes one 409: the browser logs every
// failed response, so that one line is expected rather than a defect.
let expectConflict = false;
function check(label, ok, extra = "") {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${extra ? ` — ${extra}` : ""}`);
  if (!ok) failures.push(label);
}

const PAGE = (heading) =>
  `<!doctype html><html><head><title>Cat zone</title><link rel="stylesheet" href="styles.css"></head><body><h1>${heading}</h1></body></html>`;
const V1_INDEX = PAGE("Hello cats");
// The second run's file ends with a newline, which is what makes the highlight
// layer and the edit textarea disagree about the last line if not compensated.
const V2_INDEX = `${PAGE("Hello dogs")}\n`;
const CSS = "body { background: rgb(1, 2, 3); }";
const EDITED_INDEX = PAGE("Hello hand-edited");

const CHAT = "chat-check";
// The preview URL is a capability URL scoped to the chat; it always serves the
// active iteration's files, so the iframe is the proof the switch landed.
const PREVIEW_BASE = `/api/v1/webpages/${CHAT}/preview/key-check/`;
const PREVIEW_URL = `${PREVIEW_BASE}index.html`;
const PREVIEW_TYPES = { "index.html": "text/html", "styles.css": "text/css" };

// ---------- the server side, as the checks build it up ----------
const created = "2026-01-0";
let versions = [];
let filesByVersion = {};
let activeVersionId = null;
let nextId = 1;
let staleSave = false;
const activationRequests = [];
const saveRequests = [];

function addVersion(files, { origin = "ai", label = "", parent = activeVersionId } = {}) {
  const id = `v${nextId++}`;
  const parentExists = Boolean(parent && filesByVersion[parent]);
  versions.push({
    id,
    seq: versions.length + 1,
    parentVersionId: parentExists ? parent : null,
    origin,
    label,
    messageUid: origin === "ai" ? `reply-${id}` : null,
    assets: [],
    summary: {
      files: Object.keys(files).length,
      added: Object.keys(files).length,
      removed: parentExists ? 1 : 0,
    },
    createdAt: `${created}${versions.length + 1}T10:0${versions.length}:00Z`,
  });
  filesByVersion[id] = files;
  activeVersionId = id;
  return id;
}

const envJson = () => ({
  webpageId: "webpage-check",
  files: Object.keys(filesByVersion[activeVersionId] ?? {}),
  updatedAt: "2026-01-01T00:00:00Z",
  activeVersionId,
  previewUrl: PREVIEW_URL,
  previewBase: PREVIEW_BASE,
  entry: "index.html",
  serveRoot: "",
  hasBuild: false,
  lastBuild: null,
});

function diffPayload(id) {
  const version = versions.find((v) => v.id === id);
  if (!version) return { status: 404, body: { detail: "Iteration not found" } };
  const base = version.parentVersionId ? filesByVersion[version.parentVersionId] ?? {} : {};
  const current = filesByVersion[id] ?? {};
  const files = [];
  for (const path of [...new Set([...Object.keys(base), ...Object.keys(current)])].sort()) {
    const before = (base[path] ?? "").split("\n");
    const after = (current[path] ?? "").split("\n");
    if (!base[path]) {
      files.push({
        path,
        status: "added",
        added: after.length,
        removed: 0,
        lines: after.map((text) => ({ kind: "add", text })),
      });
    } else if (!current[path]) {
      files.push({
        path,
        status: "removed",
        added: 0,
        removed: before.length,
        lines: before.map((text) => ({ kind: "del", text })),
      });
    } else if (base[path] !== current[path]) {
      files.push({
        path,
        status: "modified",
        added: after.length,
        removed: before.length,
        lines: [
          { kind: "hunk", text: `@@ -1,${before.length} +1,${after.length} @@` },
          ...before.map((text) => ({ kind: "del", text })),
          ...after.map((text) => ({ kind: "add", text })),
        ],
      });
    }
  }
  return {
    status: 200,
    body: { versionId: id, baseVersionId: version.parentVersionId ?? null, files, truncated: false },
  };
}

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
let savedChats = [];
await page.route("**/api/v1/chats", async (route) => {
  await route.fulfill(
    json(route.request().method() === "GET" ? savedChats : { id: CHAT, title: "New chat" }),
  );
});

await page.route("**/api/v1/webpages/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const parts = url.pathname.split(`/api/v1/webpages/${CHAT}/`)[1]?.split("/").filter(Boolean) ?? [];
  const [sub, ...rest] = parts;
  // The served preview: whatever the active iteration holds, so the iframe the
  // canvas loads is where the activation becomes visible.
  if (sub === "preview") {
    const name = rest.slice(1).join("/") || "index.html";
    const content = filesByVersion[activeVersionId]?.[name];
    if (typeof content === "string") {
      return route.fulfill({ status: 200, contentType: PREVIEW_TYPES[name] ?? "text/plain", body: content });
    }
    return route.fulfill(json({ detail: "Not found" }, 404));
  }
  if (sub === "versions" && rest[0]) {
    const payload = diffPayload(rest[0]);
    return route.fulfill(json(payload.body, payload.status));
  }
  if (sub === "versions") return route.fulfill(json({ activeVersionId, versions }));
  if (sub === "active" && request.method() === "PUT") {
    const body = JSON.parse(request.postData() ?? "{}");
    activationRequests.push(body);
    if (filesByVersion[body.versionId]) activeVersionId = body.versionId;
    return route.fulfill(json(envJson()));
  }
  if (sub === "files" && request.method() === "PUT") {
    const body = JSON.parse(request.postData() ?? "{}");
    saveRequests.push(body);
    // A tab whose base moved on is refused: the canvas shows the notice and
    // keeps the user's draft.
    if (staleSave) {
      return route.fulfill(
        json({ detail: "The page changed since you opened it. Reload the canvas and try again." }, 409),
      );
    }
    const next = { ...filesByVersion[activeVersionId], [body.path]: body.content };
    addVersion(next, { origin: "manual", label: `Edited ${body.path}` });
    return route.fulfill(json(envJson()));
  }
  if (sub === "files") {
    const path = rest.join("/");
    const content = filesByVersion[activeVersionId]?.[path];
    if (typeof content === "string") return route.fulfill(json({ path, content }));
    return route.fulfill(json({ detail: "File not found" }, 404));
  }
  if (!sub) return route.fulfill(json(envJson()));
  return route.fulfill(json({ detail: "Not found" }, 404));
});

// The stream: turn one builds two files, turn two rewrites index.html. Both
// mutate the server state first, so the env/versions GETs the canvas fires on
// each webpage event already see the committed iteration.
let streamTurn = 0;
let hangStream = false;
function sseTurn(writes, heading) {
  return [
    "event: start\ndata: {}\n\n",
    ...writes.flatMap(([path, content], i) => [
      `event: tool_call\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", display: "Building Webpage", args: { path, bytes: content.length }, label: `write_webpage_file(path='${path}')` })}\n\n`,
      `event: tool_result\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", content: JSON.stringify({ status: "webpage_file_written", webpage_id: "webpage-check", version_id: null, path, bytes: content.length, files: writes.map(([p]) => p).slice(0, i + 1) }), ok: true })}\n\n`,
      `event: webpage\ndata: ${JSON.stringify({ webpageId: "webpage-check", path, files: writes.map(([p]) => p).slice(0, i + 1), versionId: null, build: null, previewBase: PREVIEW_BASE, previewUrl: PREVIEW_URL })}\n\n`,
    ]),
    `event: token\ndata: ${JSON.stringify({ content: `The page now says ${heading}.` })}\n\n`,
    "event: done\ndata: {}\n\n",
  ].join("");
}

await page.route("**/api/v1/chat/stream", async (route) => {
  if (hangStream) return; // never fulfilled: the reply is still being written
  streamTurn += 1;
  let body;
  if (streamTurn === 1) {
    addVersion({ "index.html": V1_INDEX, "styles.css": CSS });
    body = sseTurn(
      [
        ["index.html", V1_INDEX],
        ["styles.css", CSS],
      ],
      "Hello cats",
    );
  } else {
    addVersion({ ...filesByVersion[activeVersionId], "index.html": V2_INDEX });
    body = sseTurn([["index.html", V2_INDEX]], "Hello dogs");
  }
  await route.fulfill({ status: 200, contentType: "text/event-stream", body });
});

async function waitFor(fn, timeout = 5000) {
  const start = Date.now();
  for (;;) {
    if (await fn()) return true;
    if (Date.now() - start > timeout) return false;
    await page.waitForTimeout(50);
  }
}

const send = async (text) => {
  await page.fill('textarea[aria-label="Message Cappuccina"]', text);
  await page.keyboard.press("Enter");
};

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });

// 1. the first run is iteration 1 of 1 and the served preview shows it
await send("make me a webpage about cats");
await page.waitForSelector(".canvas-frame", { timeout: 8000 });
const bar = () => page.locator(".canvas-history-count").textContent();
const frameHeading = () => page.frameLocator(".canvas-frame").locator("h1").textContent();
check("history bar appears for the first run", await page.locator(".canvas-history").first().isVisible());
check("iteration 1 of 1", await waitFor(async () => (await bar()) === "Iteration 1 of 1"), await bar());
check("preview URL is the frame's src", (await page.locator(".canvas-frame").getAttribute("src")) === PREVIEW_URL);
check(
  "preview serves the first page",
  await waitFor(async () => (await frameHeading().catch(() => null)) === "Hello cats"),
);

// 2. a second run is its own iteration, and the preview follows it
await send("now change the heading");
check("iteration 2 of 2", await waitFor(async () => (await bar()) === "Iteration 2 of 2"), await bar());
check(
  "preview serves the second page",
  await waitFor(async () => (await frameHeading().catch(() => null)) === "Hello dogs"),
);

// 3. stepping back activates the older iteration (server round-trip); the
//    preview reloads onto it and the Code pane shows that iteration's text
await page.click('.canvas-history-step[aria-label="Previous iteration"]');
const activated = await waitFor(() => activationRequests.length > 0);
check("previous iteration activates v1", activated, JSON.stringify(activationRequests));
check("activation body carries versionId", activationRequests[0]?.versionId === "v1");
check("bar back to iteration 1 of 2", await waitFor(async () => (await bar()) === "Iteration 1 of 2"), await bar());
check(
  "preview follows the activation",
  await waitFor(async () => (await frameHeading().catch(() => null)) === "Hello cats"),
);
await page.click('.canvas-tabs button:has-text("Code")');
await page.waitForSelector(".canvas-file-row", { timeout: 3000 });
await page.click('.canvas-file-row:has-text("index.html")');
await page.waitForSelector(".canvas-code-head", { timeout: 3000 });
const codeText = await page.locator(".canvas-code-body").textContent();
check("code pane shows the activated iteration", codeText.includes("Hello cats") && !codeText.includes("Hello dogs"));

// 4. the history button opens the full list, newest first, active row marked
await page.click('.canvas-history-btn[aria-label="All iterations"]');
await page.waitForSelector(".canvas-history-panel", { timeout: 3000 });
const rows = await page.locator(".canvas-history-panel .canvas-file-row").allTextContents();
check("history lists every iteration", rows.length === 2, rows.join(" | "));
const activeSeq = await page.locator(".canvas-history-panel .canvas-file-row.active .canvas-history-seq").textContent();
check("history marks the activated iteration", activeSeq === "#1", activeSeq);

// 5. the Changes tab diffs the active iteration against its base
await page.click('.canvas-history-btn[aria-label="All iterations"]'); // close the panel
await page.click('.canvas-history-step[aria-label="Next iteration"]');
check("next iteration activates v2", await waitFor(async () => (await bar()) === "Iteration 2 of 2"), await bar());
await page.click('.canvas-tabs button:has-text("Changes")');
await page.waitForSelector(".canvas-diff", { timeout: 3000 });
check(".canvas-diff hosts the diff", (await page.locator(".canvas-diff").count()) === 1);
check("diff shows an added line", (await page.locator(".diff-add").count()) > 0);
check("diff shows a removed line", (await page.locator(".diff-del").count()) > 0);
check("diff names the changed file", (await page.locator(".canvas-change-list").textContent()).includes("index.html"));

// 6. a manual edit saves against the rendered iteration and lands as its own
await page.click('.canvas-tabs button:has-text("Code")');
await page.waitForSelector(".canvas-code", { timeout: 3000 });
if ((await page.locator(".canvas-file-row").count()) > 0) {
  await page.click('.canvas-file-row:has-text("index.html")');
}
await page.waitForSelector(".canvas-code-head", { timeout: 3000 });
const shownName = await page.locator(".canvas-code-head .canvas-file-name").textContent();
check("code pane shows index.html before editing", shownName === "index.html", shownName);
const activeBeforeEdit = activeVersionId;
// The pencil must not restyle the pane: the highlighted layer keeps exactly the
// look the reading pane had, and the transparent textarea shares its metrics so
// the caret lands on the colored glyphs.
const styleSnapshot = (selector) =>
  page.locator(selector).evaluate((el) => {
    const s = getComputedStyle(el);
    return [
      s.fontFamily,
      s.fontSize,
      s.fontWeight,
      s.lineHeight,
      s.tabSize,
      s.fontVariantLigatures,
      s.color,
      s.backgroundColor,
      s.padding,
      s.whiteSpace,
    ].join(" | ");
  });
const codeLook = await styleSnapshot(".canvas-code-body");
const codeBox = await page.locator(".canvas-code-body").boundingBox();
const codeClip = {
  x: codeBox.x,
  y: codeBox.y,
  width: Math.min(codeBox.width - 24, 600),
  height: 40,
};
const codePixels = await page.screenshot({ clip: codeClip });
await page.click('.canvas-icon-btn[aria-label="Edit index.html"]');
await page.waitForSelector("textarea.canvas-code-edit", { timeout: 3000 });
const highlightLook = await styleSnapshot(".canvas-code-highlight");
check("edit pane keeps the code theme", codeLook === highlightLook, `\n    code:   ${codeLook}\n    edit:   ${highlightLook}`);
const highlightBox = await page.locator(".canvas-code-highlight").boundingBox();
const highlightPixels = await page.screenshot({
  clip: { ...codeClip, x: highlightBox.x, y: highlightBox.y },
});
check(
  "the highlighted text is pixel-identical while editing",
  codePixels.equals(highlightPixels),
  `${codePixels.length} vs ${highlightPixels.length} bytes`,
);
const editMetrics = await page.evaluate(() => {
  const pre = document.querySelector(".canvas-code-highlight");
  const area = document.querySelector("textarea.canvas-code-edit");
  const a = getComputedStyle(pre);
  const b = getComputedStyle(area);
  const keys = ["fontFamily", "fontSize", "lineHeight", "tabSize", "fontVariantLigatures", "padding", "whiteSpace"];
  const padRight = parseFloat(b.paddingRight) || 0;
  return {
    sameFont: keys.every((k) => a[k] === b[k]),
    // A textarea's scroll width stops at its last character (the trailing
    // padding is not scrollable content) while a pre's includes it, so the two
    // are compared after removing that padding. The line grid must match
    // exactly — that is the trailing-newline compensation doing its job.
    sameTextWidth: Math.abs(pre.scrollWidth - padRight - area.scrollWidth) <= 1,
    sameLines: pre.scrollHeight === area.scrollHeight,
    extent: `pre ${pre.scrollWidth}x${pre.scrollHeight} (pad-right ${padRight}) vs textarea ${area.scrollWidth}x${area.scrollHeight}`,
    // Whatever the highlighter wraps tokens in, a colored span proves the pane
    // still paints per-token colours while editing.
    spans: (() => {
      const layerColor = a.color;
      const all = [...pre.querySelectorAll("span")];
      return {
        total: all.length,
        colored: all.filter((s) => getComputedStyle(s).color !== layerColor).length,
      };
    })(),
  };
});
check(
  "the editor text sits on the highlighted glyphs",
  editMetrics.sameFont && editMetrics.sameTextWidth && editMetrics.sameLines,
  editMetrics.extent,
);
check(
  "colors are rendered while editing",
  editMetrics.spans.total > 0 && editMetrics.spans.colored > 0,
  `${editMetrics.spans.total} spans, ${editMetrics.spans.colored} colored`,
);
// The two layers are separate scrollers: the textarea drives, the highlight
// layer follows, so the text stays behind the caret.
await page.locator("textarea.canvas-code-edit").evaluate((el) => {
  el.scrollLeft = 200;
});
const scrolled = await waitFor(async () =>
  (await page.evaluate(() => ({
    pre: document.querySelector(".canvas-code-highlight").scrollLeft,
    area: document.querySelector("textarea.canvas-code-edit").scrollLeft,
  }))).pre === 200,
);
check("the highlight layer scrolls with the editor", scrolled);
await page.fill("textarea.canvas-code-edit", EDITED_INDEX);
const followsTyping = await page.evaluate(
  () =>
    document.querySelector(".canvas-code-highlight").textContent ===
    document.querySelector("textarea.canvas-code-edit").value,
);
check("the highlighting follows what is typed", followsTyping);
await page.click(".canvas-edit-save");
check("save posts a new iteration", await waitFor(() => saveRequests.length > 0));
check("save carries the rendered baseVersionId", saveRequests[0]?.baseVersionId === activeBeforeEdit, `${saveRequests[0]?.baseVersionId} vs ${activeBeforeEdit}`);
check("bar reads iteration 3 of 3", await waitFor(async () => (await bar()) === "Iteration 3 of 3"), await bar());
await page.click('.canvas-history-btn[aria-label="All iterations"]');
await page.waitForSelector(".canvas-history-panel", { timeout: 3000 });
const editedRow = await page.locator('.canvas-history-panel .canvas-file-row .canvas-file-name', { hasText: "Edited index.html" }).count();
check("history shows the manual edit", editedRow === 1);
await page.click('.canvas-history-btn[aria-label="All iterations"]');

// 6b. a stale base is refused with a 409 and the canvas says why, keeping the
//     draft instead of silently reloading over the user's text
staleSave = true;
expectConflict = true;
await page.click('.canvas-icon-btn[aria-label="Edit index.html"]');
await page.waitForSelector("textarea.canvas-code-edit", { timeout: 3000 });
await page.fill("textarea.canvas-code-edit", `${EDITED_INDEX}<!-- again -->`);
await page.click(".canvas-edit-save");
const notice = await waitFor(async () => {
  const text = await page.locator(".canvas-notice").first().textContent().catch(() => "");
  return (text ?? "").includes("The page changed since you opened it");
});
check("stale save shows the 409 notice", notice, await page.locator(".canvas-notice").first().textContent().catch(() => ""));
check("the draft survives the refusal", (await page.locator("textarea.canvas-code-edit").count()) === 1);
await page.click(".canvas-edit-cancel");
staleSave = false;
expectConflict = false;

// 7. while a reply is being written nothing can be switched or edited
hangStream = true;
await send("tell me a long story");
const previousDisabled = await waitFor(() =>
  page.locator('.canvas-history-step[aria-label="Previous iteration"]').isDisabled(),
);
const allDisabled = await page.locator('.canvas-history-btn[aria-label="All iterations"]').isDisabled();
const editDisabled = await page.locator('.canvas-icon-btn[aria-label="Edit index.html"]').isDisabled();
check("previous step disabled while streaming", previousDisabled);
check("history button disabled while streaming", allDisabled);
check("edit button disabled while streaming", editDisabled);

check("no console errors", errors.length === 0, errors.join(" | "));

await browser.close();

if (failures.length) {
  console.log(`\n${failures.length} failed: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("\nall checks passed");
