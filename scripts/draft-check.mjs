import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

await page.route("**/api/v1/auth/me", async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      id: "check",
      email: "check@example.com",
      displayName: "Check",
      gmailConnected: true,
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

const draft = {
  status: "draft_created",
  draft_id: "draft-check",
  message_id: "m-1",
  to: "teacher@school.edu",
  subject: "About the science project",
  body: "Hi! Could I get an extra day for the science project? Thank you!",
};

await page.route("**/api/v1/chat/stream", async (route) => {
  const sse = [
    "event: start\ndata: {}\n\n",
    'event: reasoning\ndata: {"content":"Writes a polite extension request. "}\n\n',
    `event: tool_call\ndata: ${JSON.stringify({
      id: "t1",
      name: "draft_email",
      display: "Compose Email",
      args: { to: draft.to, subject: draft.subject, body: draft.body },
      label: "draft_email",
    })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({
      id: "t1",
      name: "draft_email",
      content: JSON.stringify(draft),
      ok: true,
    })}\n\n`,
    'event: token\ndata: {"content":"I saved a draft — press Send when you\'re happy with it!"}\n\n',
    "event: done\ndata: {}\n\n",
  ].join("");
  await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
});

let updateCalls = 0;
let lastUpdate = null;
await page.route("**/api/v1/gmail/drafts/draft-check", async (route) => {
  updateCalls += 1;
  lastUpdate = route.request().postDataJSON();
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ok: true, ...lastUpdate }),
  });
});

// Registered last so it wins over the broader update pattern above.
let sendCalls = 0;
await page.route("**/api/v1/gmail/drafts/draft-check/send", async (route) => {
  sendCalls += 1;
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ok: true, message_id: "m-1", thread_id: "t-1" }),
  });
});

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await page.fill('input[aria-label="Message Cappuccina"]', "email my teacher for an extension");
await page.keyboard.press("Enter");

await page.waitForSelector(".draft-card", { timeout: 5000 });
const cardText = await page.locator(".draft-card").first().textContent();
console.log("card shows recipient:", cardText.includes("teacher@school.edu"));
console.log("card shows subject:", cardText.includes("About the science project"));
console.log("card shows body:", cardText.includes("extra day for the science project"));
console.log("card has send button:", (await page.locator(".draft-send").count()) === 1);

// The raw tool block must be hidden so the draft isn't shown twice.
await page.click(".thinking-head");
await page.waitForTimeout(200);
const rawToolBlocks = await page.locator(".thinking-body .tool-call").count();
console.log("no duplicate raw tool block:", rawToolBlocks === 0);

// --- edit mode -----------------------------------------------------------
await page.click(".draft-edit");
await page.waitForSelector(".draft-edit-form", { timeout: 3000 });
const editFormShown = (await page.locator(".draft-edit-form").count()) === 1;
console.log("edit form opens:", editFormShown);

await page.fill('[aria-label="Subject"]', "Science project — one more day?");
await page.fill(
  '[aria-label="Message body"]',
  "Hi! Could I get an extra day for the science project? Thank you! — Sam",
);
await page.click('button:has-text("Save changes")');
await page.waitForSelector(".draft-edit-form", { state: "detached", timeout: 3000 });
console.log("save calls update endpoint:", updateCalls === 1);
console.log(
  "saved body includes the edit:",
  lastUpdate?.body?.includes("— Sam") === true,
);
const cardAfterSave = await page.locator(".draft-card").first().textContent();
console.log("card reflects edited subject:", cardAfterSave.includes("one more day"));

const sentBeforeClick = (await page.locator(".draft-note.ok").count()) === 0;
console.log("nothing sent before click:", sentBeforeClick);

await page.click('button:has-text("Send email")');
await page.waitForSelector(".draft-note.ok", { timeout: 5000 });
const sentText = await page.locator(".draft-note.ok").first().textContent();
console.log("shows sent state:", sentText.includes("Sent"));
console.log("send endpoint called once:", sendCalls === 1);
console.log("no extra update on send after save:", updateCalls === 1);

// --- reload persistence: server returns the part stamped sent ------------
savedChats = [
  {
    id: "chat-check",
    title: "New chat",
    mode: "chat",
    subject: null,
    messages: [
      { role: "user", content: "email my teacher for an extension" },
      {
        role: "assistant",
        content: "I saved a draft — press Send when you're happy with it!",
        reasoning: "Writes a polite extension request. ",
        tools: [
          {
            id: "t1",
            name: "draft_email",
            display: "Compose Email",
            args: {},
            result: JSON.stringify(draft),
            sent: true,
          },
        ],
        parts: [
          { type: "reasoning", text: "Writes a polite extension request. " },
          {
            type: "tool",
            id: "t1",
            name: "draft_email",
            display: "Compose Email",
            args: {},
            result: JSON.stringify(draft),
            sent: true,
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
const sentAfterReload = (await page.locator(".draft-note.ok").count()) === 1;
const noSendAfterReload = (await page.locator(".draft-send").count()) === 0;
console.log("reload shows sent:", sentAfterReload);
console.log("reload hides send button:", noSendAfterReload);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();

if (
  !cardText.includes("teacher@school.edu") ||
  rawToolBlocks !== 0 ||
  !editFormShown ||
  updateCalls !== 1 ||
  lastUpdate?.body?.includes("— Sam") !== true ||
  !cardAfterSave.includes("one more day") ||
  !sentBeforeClick ||
  sendCalls !== 1 ||
  !sentAfterReload ||
  !noSendAfterReload ||
  errors.length
) {
  process.exit(1);
}
