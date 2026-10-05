// Canvas iteration history: stepping through the runs, the Changes tab, and a
// manual edit landing as its own iteration (see backend/app/main.py routes).
// Run against `npm run preview` (port 4173): node scripts/webpage-versions-check.mjs
import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

const failures = [];
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

// ---------- the server side, as the checks build it up ----------
const created = "2026-01-0";
let versions = [];
let filesByVersion = {};
let activeVersionId = null;
let nextId = 1;
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
  webpageId: "env-check",
  files: filesByVersion[activeVersionId] ?? {},
  updatedAt: "2026-01-01T00:00:00Z",
  activeVersionId,
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
    json(route.request().method() === "GET" ? savedChats : { id: "chat-check", title: "New chat" }),
  );
});

await page.route("**/api/v1/webpages/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const parts = url.pathname.split("/api/v1/webpages/")[1]?.split("/").filter(Boolean) ?? [];
  const sub = parts[1];
  if (!sub) return route.fulfill(json(envJson()));
  if (sub === "versions" && parts[2]) {
    const payload = diffPayload(parts[2]);
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
    const next = { ...filesByVersion[activeVersionId], [body.path]: body.content };
    const id = addVersion(next, { origin: "manual", label: `Edited ${body.path}` });
    return route.fulfill(json({ ...envJson(), webpageId: "env-check", activeVersionId: id }));
  }
  return route.fulfill(json({ detail: "Not found" }, 404));
});

// The stream: turn one builds two files, turn two rewrites index.html. Both
// mutate the server state first, so the versions GET that the canvas fires on
// each webpage event already sees the committed iteration.
let streamTurn = 0;
let hangStream = false;
function sseTurn(versionId, writes, heading) {
  return [
    "event: start\ndata: {}\n\n",
    ...writes.flatMap(([path, content], i) => [
      `event: tool_call\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", display: "Building Webpage", args: { path, bytes: content.length }, label: `write_webpage_file(path='${path}')` })}\n\n`,
      `event: tool_result\ndata: ${JSON.stringify({ id: `t${i}`, name: "write_webpage_file", content: JSON.stringify({ status: "webpage_file_written", webpage_id: "env-check", version_id: versionId, path, bytes: content.length, files: writes.map(([p]) => p).slice(0, i + 1) }), ok: true })}\n\n`,
      `event: webpage\ndata: ${JSON.stringify({ webpageId: "env-check", path, files: writes.map(([p]) => p).slice(0, i + 1), content, versionId })}\n\n`,
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
    const id = addVersion({ "index.html": V1_INDEX, "styles.css": CSS });
    body = sseTurn(
      id,
      [
        ["index.html", V1_INDEX],
        ["styles.css", CSS],
      ],
      "Hello cats",
    );
  } else {
    const id = addVersion({ ...filesByVersion[activeVersionId], "index.html": V2_INDEX });
    body = sseTurn(id, [["index.html", V2_INDEX]], "Hello dogs");
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

// 1. the first run is iteration 1 of 1 and the preview shows the built page
await send("make me a webpage about cats");
await page.waitForSelector(".canvas-frame", { timeout: 8000 });
const bar = () => page.locator(".canvas-history-count").textContent();
check("history bar appears for the first run", await page.locator(".canvas-history").first().isVisible());
check("iteration 1 of 1", await waitFor(async () => (await bar()) === "Iteration 1 of 1"), await bar());
const frame = page.frameLocator(".canvas-frame");
check(
  "preview shows the first page",
  (await frame.locator("h1").textContent({ timeout: 3000 })) === "Hello cats",
);

// 2. a second run is its own iteration, and the preview follows it
await send("now change the heading");
check("iteration 2 of 2", await waitFor(async () => (await bar()) === "Iteration 2 of 2"), await bar());
check(
  "preview shows the second page",
  await waitFor(async () =>
    (await page.locator(".canvas-frame").getAttribute("srcdoc")).includes("<h1>Hello dogs</h1>"),
  ),
);

// 3. stepping back activates the older iteration (server round-trip) and the
//    Code pane shows that iteration's text
await page.click('.canvas-history-step[aria-label="Previous iteration"]');
const activated = await waitFor(() => activationRequests.length > 0);
check("previous iteration activates v1", activated, JSON.stringify(activationRequests));
check("activation body carries versionId", activationRequests[0]?.versionId === "v1");
check("bar back to iteration 1 of 2", await waitFor(async () => (await bar()) === "Iteration 1 of 2"), await bar());
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
