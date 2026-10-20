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

// unauthenticated preview: mock the session + empty chat list
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

await page.route("**/api/v1/chat/stream", async (route) => {
  const sse = [
    "event: start\ndata: {}\n\n",
    'event: reasoning\ndata: {"content":"First thought about the UK. "}\n\n',
    'event: tool_call\ndata: {"id":"t1","name":"web_search","display":"Web Search","args":{"query":"London UK weather"},"label":"web_search"}\n\n',
    'event: tool_result\ndata: {"id":"t1","name":"web_search","content":"UK result: rain 21C","ok":true}\n\n',
    'event: reasoning\ndata: {"content":"Second thought about Ontario. "}\n\n',
    'event: tool_call\ndata: {"id":"t2","name":"web_search","display":"Web Search","args":{"query":"London Ontario weather"},"label":"web_search"}\n\n',
    'event: tool_result\ndata: {"id":"t2","name":"web_search","content":"ON result: cloudy 24C","ok":true}\n\n',
    'event: token\ndata: {"content":"Both Londons covered."}\n\n',
    "event: done\ndata: {}\n\n",
  ].join("");
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sse,
  });
});

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await page.fill('textarea[aria-label="Message Cappuccina"]', "weather in both Londons");
await page.keyboard.press("Enter");

// wait for the thinking header, then expand it
await page.waitForSelector(".thinking-head", { timeout: 5000 });
await page.click(".thinking-head");
await page.waitForSelector(".thinking-body", { timeout: 5000 });

// 1. interleaved order: reasoning, tool, reasoning, tool
const order = await page.evaluate(() =>
  [...document.querySelectorAll(".thinking-body > *")].map((el) =>
    el.classList.contains("thinking-reasoning")
      ? "reasoning"
      : el.classList.contains("tool-call")
        ? "tool"
        : el.className,
  ),
);
console.log("parts order:", order.join(" | "));
const interleaved =
  order.length === 4 &&
  order[0] === "reasoning" &&
  order[1] === "tool" &&
  order[2] === "reasoning" &&
  order[3] === "tool";
console.log("interleaved reasoning/tool order:", interleaved);

// 2. tool bodies collapsed by default
const bodiesBefore = await page.locator(".thinking-body .tool-body").count();
console.log("tool bodies visible before expand:", bodiesBefore);

// 3. expand first tool: shows Input args + Result
await page.locator(".thinking-body .tool-call-head").first().click();
await page.waitForSelector(".thinking-body .tool-body", { timeout: 3000 });
const firstBody = await page.locator(".thinking-body .tool-body").first().textContent();
console.log("first tool shows args:", firstBody.includes("London UK weather"));
console.log("first tool shows result:", firstBody.includes("UK result: rain 21C"));

// 4. reasoning segments carry the right text in the right slots
const segments = await page.locator(".thinking-reasoning").allTextContents();
console.log("first reasoning mentions UK:", segments[0]?.includes("UK"));
console.log("second reasoning mentions Ontario:", segments[1]?.includes("Ontario"));

// 5. collapse again hides the body
await page.locator(".thinking-body .tool-call-head").first().click();
await page.waitForTimeout(200);
console.log(
  "tool body hidden after collapse:",
  (await page.locator(".thinking-body .tool-body").count()) === 0,
);

// 6. simulated restart: server now returns the finished turn with persisted
// thinking flags; the header must read "Thought for ..." instead of "Thinking..."
savedChats = [
  {
    id: "chat-check",
    title: "New chat",
    mode: "chat",
    subject: null,
    messages: [
      { role: "user", content: "weather in both Londons" },
      {
        role: "assistant",
        content: "Both Londons covered.",
        reasoning: "First thought about the UK. Second thought about Ontario. ",
        tools: [
          {
            id: "t1",
            name: "web_search",
            display: "Web Search",
            args: { query: "London UK weather" },
            result: "UK result: rain 21C",
            ok: true,
          },
          {
            id: "t2",
            name: "web_search",
            display: "Web Search",
            args: { query: "London Ontario weather" },
            result: "ON result: cloudy 24C",
            ok: true,
          },
        ],
        parts: [
          { type: "reasoning", text: "First thought about the UK. " },
          {
            type: "tool",
            id: "t1",
            name: "web_search",
            display: "Web Search",
            args: { query: "London UK weather" },
            result: "UK result: rain 21C",
            ok: true,
          },
          { type: "reasoning", text: "Second thought about Ontario. " },
          {
            type: "tool",
            id: "t2",
            name: "web_search",
            display: "Web Search",
            args: { query: "London Ontario weather" },
            result: "ON result: cloudy 24C",
            ok: true,
          },
        ],
        thinkingDone: true,
        thoughtSeconds: 4.5,
      },
    ],
  },
];
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector(".thinking-head", { timeout: 5000 });
const headText = await page.locator(".thinking-head").first().textContent();
console.log("header after reload:", headText.trim());
const keptDuration = headText.includes("Thought for 4.5 seconds");
console.log("finished label survives reload:", keptDuration);

// 7. interleaving also survives the reload
await page.click(".thinking-head");
const orderAfter = await page.evaluate(() =>
  [...document.querySelectorAll(".thinking-body > *")].map((el) =>
    el.classList.contains("thinking-reasoning")
      ? "reasoning"
      : el.classList.contains("tool-call")
        ? "tool"
        : el.className,
  ),
);
const interleavedAfter =
  orderAfter.length === 4 &&
  orderAfter[0] === "reasoning" &&
  orderAfter[1] === "tool" &&
  orderAfter[2] === "reasoning" &&
  orderAfter[3] === "tool";
console.log("interleaved after reload:", interleavedAfter);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();

if (!interleaved || bodiesBefore !== 0 || !keptDuration || !interleavedAfter) process.exit(1);
