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
// "Failed to load resource" is browser noise for endpoints the mocks leave out.
page.on(
  "console",
  (m) =>
    m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(m.text()),
);
page.on("pageerror", (e) => errors.push(String(e)));

await page.route("**/api/v1/auth/me", async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      id: "check",
      email: "check@example.com",
      displayName: "Check",
      calendarConnected: true,
    }),
  });
});

let savedChats = [];
await page.route("**/api/v1/chats", async (route) => {
  const body =
    route.request().method() === "GET"
      ? JSON.stringify(savedChats)
      : JSON.stringify({ id: "chat-check", title: "New chat" });
  await route.fulfill({ status: 200, contentType: "application/json", body });
});

const proposal = {
  status: "delete_proposed",
  event_id: "evt-1",
  title: "Piano lesson",
  when: "2026-09-22T16:00:00+02:00 → 2026-09-22T17:00:00+02:00",
  location: "Music room",
};

await page.route("**/api/v1/chat/stream", async (route) => {
  const sse = [
    "event: start\ndata: {}\n\n",
    'event: reasoning\ndata: {"content":"Looks up the event, then asks to confirm. "}\n\n',
    `event: tool_call\ndata: ${JSON.stringify({
      id: "t1",
      name: "propose_event_deletion",
      display: "Delete Event",
      args: { event_id: proposal.event_id },
      label: "propose_event_deletion",
    })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({
      id: "t1",
      name: "propose_event_deletion",
      content: JSON.stringify(proposal),
      ok: true,
    })}\n\n`,
    'event: token\ndata: {"content":"I prepared the deletion — press Delete to confirm."}\n\n',
    "event: done\ndata: {}\n\n",
  ].join("");
  await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
});

let deleteCalls = 0;
await page.route("**/api/v1/calendar/events/evt-1/delete", async (route) => {
  deleteCalls += 1;
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ok: true, event_id: "evt-1" }),
  });
});

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await page.fill('textarea[aria-label="Message Cappuccina"]', "delete my piano lesson");
await page.keyboard.press("Enter");

await page.waitForSelector(".draft-card", { timeout: 5000 });
const cardText = await page.locator(".draft-card").first().textContent();
console.log("card shows event title:", cardText.includes("Piano lesson"));
console.log("card shows when:", cardText.includes("2026-09-22T16:00:00+02:00"));
console.log("card shows location:", cardText.includes("Music room"));
console.log("card has delete button:", (await page.locator(".draft-send.danger").count()) === 1);

// The raw tool block must be hidden so the proposal isn't shown twice.
await page.click(".thinking-head");
await page.waitForTimeout(200);
const rawToolBlocks = await page.locator(".thinking-body .tool-call").count();
console.log("no duplicate raw tool block:", rawToolBlocks === 0);

const deletedBeforeClick = (await page.locator(".draft-note.ok").count()) === 0;
console.log("nothing deleted before click:", deletedBeforeClick);

await page.click('button:has-text("Delete event")');
await page.waitForSelector(".draft-note.ok", { timeout: 5000 });
const deletedText = await page.locator(".draft-note.ok").first().textContent();
console.log("shows deleted state:", deletedText.includes("Deleted"));
console.log("delete endpoint called once:", deleteCalls === 1);

// --- reload persistence: server returns the part stamped deleted ---------
savedChats = [
  {
    id: "chat-check",
    title: "New chat",
    mode: "chat",
    subject: null,
    messages: [
      { role: "user", content: "delete my piano lesson" },
      {
        role: "assistant",
        content: "I prepared the deletion — press Delete to confirm.",
        reasoning: "Looks up the event, then asks to confirm. ",
        tools: [
          {
            id: "t1",
            name: "propose_event_deletion",
            display: "Delete Event",
            args: {},
            result: JSON.stringify(proposal),
            deleted: true,
          },
        ],
        parts: [
          { type: "reasoning", text: "Looks up the event, then asks to confirm. " },
          {
            type: "tool",
            id: "t1",
            name: "propose_event_deletion",
            display: "Delete Event",
            args: {},
            result: JSON.stringify(proposal),
            deleted: true,
          },
        ],
        thinkingDone: true,
        thoughtSeconds: 2.0,
      },
    ],
  },
];
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector(".draft-card", { timeout: 5000 });
const deletedAfterReload = (await page.locator(".draft-note.ok").count()) === 1;
const noDeleteAfterReload = (await page.locator(".draft-send.danger").count()) === 0;
console.log("reload shows deleted:", deletedAfterReload);
console.log("reload hides delete button:", noDeleteAfterReload);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();

if (
  !cardText.includes("Piano lesson") ||
  !cardText.includes("Music room") ||
  rawToolBlocks !== 0 ||
  !deletedBeforeClick ||
  deleteCalls !== 1 ||
  !deletedAfterReload ||
  !noDeleteAfterReload ||
  errors.length
) {
  process.exit(1);
}
