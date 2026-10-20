import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
// A first visit shows the language picker; pick English up front.
await page.addInitScript(() => {
  try {
    localStorage.setItem("cappuccina-lang", "en");
  } catch {}
});
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

let capturedBody = null;
await page.route("**/api/v1/chat/stream", async (route) => {
  capturedBody = route.request().postDataJSON();
  const sse = [
    "event: start\ndata: {}\n\n",
    'event: token\ndata: {"content":"Small steps first. What is 4x + 2 = 18? Think: take 2 from both sides."}\n\n',
    "event: done\ndata: {}\n\n",
  ].join("");
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sse,
  });
});

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });

// default state: Chat segment active, no subject input
const chatActive = await page
  .locator('.mode-seg .seg:has-text("Chat")')
  .evaluate((el) => el.classList.contains("active"));
console.log("chat segment active by default:", chatActive);
console.log("subject input hidden by default:", (await page.locator(".subject-input").count()) === 0);

// switch to Tutor
await page.click('.mode-seg .seg:has-text("Tutor")');
const subjectInput = page.locator(".subject-input");
console.log("subject pill removed:", (await subjectInput.count()) === 0);

// tutor welcome copy
await page.waitForFunction(() =>
  document.querySelector(".welcome-sub")?.textContent.includes("what you'd like to learn"),
);
console.log("tutor welcome copy: shown");

// main input placeholder cues the subject
const placeholder = await page.getAttribute('textarea[aria-label="Message Cappuccina"]', "placeholder");
console.log("tutor placeholder:", placeholder === "What shall we learn today?");

// TUTOR badge appears in the sidebar chat list
const badge = page.locator(".chat-list-item.active .tag.tutor");
console.log("TUTOR badge visible:", (await badge.count()) === 1, await badge.textContent());

// send a message; the subject is typed in the main box but not parsed out — null subject
await page.fill('textarea[aria-label="Message Cappuccina"]', "help me with fractions");
await page.keyboard.press("Enter");
await page.waitForFunction(
  () => [...document.querySelectorAll(".bubble")].some((b) => b.textContent.includes("4x")),
  { timeout: 5000 },
);
console.log("request mode:", capturedBody?.mode, "| subject:", capturedBody?.subject);
console.log(
  "assistant reply rendered:",
  capturedBody?.mode === "tutor" && capturedBody?.subject === null,
);

// allow the debounced IndexedDB save to flush so the reload test is meaningful
await page.waitForTimeout(600);

// persistence: reload, same chat, tutor still active with subject + badge
await page.reload({ waitUntil: "networkidle" });

await page.waitForFunction(
  () =>
    document.querySelector(".chat-list-item .tag.tutor") !== null &&
    [...document.querySelectorAll(".mode-seg .seg")].some(
      (b) => b.textContent === "Tutor" && b.classList.contains("active"),
    ),
  { timeout: 3000 },
);
console.log("persisted after reload: badge + tutor active");
console.log(
  "no subject pill after reload:",
  (await page.locator(".subject-input").count()) === 0,
);

// switch back to Chat: badge disappears
await page.click('.mode-seg .seg:has-text("Chat")');
await page.waitForTimeout(400); // allow the debounced IndexedDB save
await page.waitForFunction(
  () => document.querySelector(".chat-list-item .tag.tutor") === null,
);
console.log("badge cleared on switching back to chat:", true);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();
