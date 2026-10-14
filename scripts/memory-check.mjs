import { chromium } from "playwright";

// Memory feature: the chat card for a saved memory (with Undo and Manage), and
// the Memory section of Settings (list, edit, delete confirm, add, on/off).
// Run against a built preview: npm run build && npm run preview -- --port 4173

// Every `console.log("label:", boolean)` below is a check; a false one fails the run.
const failures = [];
const log = console.log;
console.log = (label, value, ...rest) => {
  log(label, value, ...rest);
  if (value === false) failures.push(label);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
// A first visit shows the language picker; pick English up front.
// Optional: SHOTS_DIR=<dir> saves screenshots; THEME=dark checks the dark theme.
const shotsDir = process.env.SHOTS_DIR;
const theme = process.env.THEME || "light";
const shot = (name) => shotsDir && page.screenshot({ path: `${shotsDir}/memory-${theme}-${name}.png` });
await page.addInitScript((theme) => {
  try {
    localStorage.setItem("cappuccina-lang", "en");
    localStorage.setItem("cappuccina-theme", theme);
  } catch {}
}, theme);
// "Failed to load resource" is browser noise for unmocked endpoints and for the
// 404 the Undo test provokes on purpose; real script errors still fail the run.
page.on(
  "console",
  (m) =>
    m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(m.text()),
);
page.on("pageerror", (e) => errors.push(String(e)));

const json = (route, body, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

let memoryEnabled = true;
const user = () => ({
  id: "check",
  email: "check@example.com",
  displayName: "Check",
  memoryEnabled,
});
const toggleBodies = [];
await page.route("**/api/v1/auth/me", async (route) => {
  if (route.request().method() === "PATCH") {
    const body = route.request().postDataJSON();
    toggleBodies.push(body);
    if (typeof body.memoryEnabled === "boolean") memoryEnabled = body.memoryEnabled;
  }
  await json(route, user());
});

await page.route("**/api/v1/chats", async (route) => {
  const body = route.request().method() === "GET" ? [] : { id: "chat-check", title: "New chat" };
  await json(route, body);
});

// ---- memory backend (in memory) ------------------------------------------
let nextId = 2;
const stamp = "2026-10-14T10:00:00+00:00";
let memories = [{ id: "m-old", content: "Has a dog named Biscuit", createdAt: stamp, updatedAt: stamp }];
const calls = { delete: [], patch: [], post: [] };

await page.route("**/api/v1/memories", async (route) => {
  const method = route.request().method();
  if (method === "GET") {
    return json(route, { memories, enabled: memoryEnabled, limit: 200, maxChars: 300 });
  }
  if (method === "POST") {
    const { content } = route.request().postDataJSON();
    calls.post.push(content);
    const created = { id: `m-${nextId++}`, content, createdAt: stamp, updatedAt: stamp };
    memories = [created, ...memories];
    return json(route, created, 201);
  }
  if (method === "DELETE") {
    memories = [];
    return json(route, { ok: true, deleted: 0 });
  }
  return route.fallback();
});
await page.route("**/api/v1/memories/*", async (route) => {
  const id = route.request().url().split("/").pop();
  const method = route.request().method();
  if (method === "DELETE") {
    calls.delete.push(id);
    const existed = memories.some((m) => m.id === id);
    memories = memories.filter((m) => m.id !== id);
    return existed
      ? json(route, { ok: true })
      : json(route, { detail: "That memory is already gone." }, 404);
  }
  if (method === "PATCH") {
    const { content } = route.request().postDataJSON();
    calls.patch.push({ id, content });
    memories = memories.map((m) => (m.id === id ? { ...m, content } : m));
    return json(route, memories.find((m) => m.id === id));
  }
  return route.fallback();
});

// ---- chat: the AI saves a memory -----------------------------------------
const saved = { status: "saved", id: "m-chat", content: "Likes drawing cats" };
memories = [{ id: "m-chat", content: saved.content, createdAt: stamp, updatedAt: stamp }, ...memories];
await page.route("**/api/v1/chat/stream", async (route) => {
  const sse = [
    "event: start\ndata: {}\n\n",
    `event: tool_call\ndata: ${JSON.stringify({
      id: "t1",
      name: "save_memory",
      display: "Saving Memory",
      args: { content: saved.content },
      label: "save_memory",
    })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({
      id: "t1",
      name: "save_memory",
      content: JSON.stringify(saved),
      ok: true,
    })}\n\n`,
    'event: token\ndata: {"content":"Got it, I\'ll remember that!"}\n\n',
    "event: done\ndata: {}\n\n",
  ].join("");
  await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
});

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await page.fill('textarea[aria-label="Message Cappuccina"]', "remember that I like drawing cats");
await page.keyboard.press("Enter");

await page.waitForSelector(".memory-card", { timeout: 5000 });
const cardText = await page.locator(".memory-card").first().textContent();
console.log("card shows the memory:", cardText.includes("Remembered: Likes drawing cats"));
console.log("card has Undo:", (await page.locator(".memory-card-undo").count()) === 1);
await page.click(".thinking-head").catch(() => {});
await page.waitForTimeout(200);
console.log("no duplicate raw tool block:", (await page.locator(".thinking-body .tool-call").count()) === 0);

await shot("card");

// Manage opens Settings on the Memory section.
await page.click(".memory-card-manage");
await page.waitForSelector(".memory-list", { timeout: 5000 });
await shot("settings");
const activeNav = await page.locator(".settings-nav-item.active").textContent();
console.log("Manage opens the Memory section:", activeNav.includes("Memory"));
console.log("list shows both memories:", (await page.locator(".memory-row").count()) === 2);
console.log("shows the count:", (await page.locator(".memory-count").textContent()).includes("2 of 200"));

// Edit a memory: Enter saves, and it moves to the top.
await page.click('button[aria-label^="Edit memory: Has a dog"]');
await page.fill(".memory-edit input", "Has a dog named Biscuit and a cat");
await page.keyboard.press("Enter");
await page.waitForSelector('.memory-text:has-text("and a cat")', { timeout: 5000 });
console.log(
  "edit sent PATCH:",
  calls.patch.length === 1 &&
    calls.patch[0].id === "m-old" &&
    calls.patch[0].content === "Has a dog named Biscuit and a cat",
);
console.log(
  "edited memory moved to the top:",
  (await page.locator(".memory-row .memory-text").first().textContent()).includes("and a cat"),
);

// Escape in the edit field cancels the edit but keeps Settings open.
await page.click('button[aria-label^="Edit memory: Likes drawing"]');
await page.keyboard.press("Escape");
console.log("Escape cancels the edit only:", (await page.locator(".memory-edit").count()) === 0 && (await page.locator(".settings").count()) === 1);

// Delete asks first, and "Keep it" changes nothing.
await page.click('button[aria-label^="Delete memory: Likes drawing"]');
console.log("delete asks to confirm:", (await page.locator(".memory-confirm").count()) === 1);
await shot("confirm");
await page.click('button:has-text("Keep it")');
console.log("keep leaves the row:", (await page.locator(".memory-row").count()) === 2 && calls.delete.length === 0);
await page.click('button[aria-label^="Delete memory: Likes drawing"]');
await page.click('button:has-text("Yes, delete")');
await page.waitForFunction(() => document.querySelectorAll(".memory-row").length === 1);
console.log("confirmed delete calls DELETE:", calls.delete.length === 1 && calls.delete[0] === "m-chat");

// Add a memory.
await page.fill('input[aria-label="Add a memory"]', "Is learning the guitar");
await page.click('button:has-text("Add")');
await page.waitForSelector('.memory-text:has-text("learning the guitar")', { timeout: 5000 });
console.log("add sent POST:", calls.post.length === 1 && calls.post[0] === "Is learning the guitar");
console.log("add cleared the field:", (await page.inputValue('input[aria-label="Add a memory"]')) === "");

// Turn memory off.
await page.click(".memory-switch");
await page.waitForSelector(".memory-list.paused", { timeout: 5000 });
console.log("toggle sent PATCH memoryEnabled=false:", toggleBodies.some((b) => b.memoryEnabled === false));
console.log("paused hint shown:", (await page.locator("text=Memory is off").count()) === 1);
await page.click(".memory-switch");
await page.waitForFunction(() => !document.querySelector(".memory-list.paused"));
console.log("toggle back on:", toggleBodies.some((b) => b.memoryEnabled === true));

// Delete all.
await page.click('button:has-text("Delete all memories")');
await page.click('button:has-text("Yes, delete all")');
await page.waitForSelector("text=Nothing saved yet", { timeout: 5000 });
console.log("delete all empties the list:", memories.length === 0);

// ---- Undo on the chat card ------------------------------------------------
// The memory was deleted in Settings, so Undo gets a 404 and still lands in "Undone".
await page.click(".settings-close");
await page.click(".memory-card-undo");
await page.waitForSelector(".memory-card .draft-note.ok", { timeout: 5000 });
console.log("Undo (already gone) shows Undone:", (await page.locator(".memory-card .draft-note.ok").textContent()).includes("Undone"));
console.log("Undo hides the button:", (await page.locator(".memory-card-undo").count()) === 0);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();

if (errors.length || failures.length) {
  log("failed checks:", failures);
  process.exit(1);
}
