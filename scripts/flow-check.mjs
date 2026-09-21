import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });

// count bubbles, send a message
const before = await page.locator(".bubble").count();
await page.fill('textarea[aria-label="Message Cappuccina"]', "I have a math test tomorrow");
await page.keyboard.press("Enter");

// assistant placeholder (animated dots) should show while the reply streams
await page.waitForSelector(".bubble-waiting .streaming", { timeout: 3000 });
console.log("assistant placeholder: shown");

// assistant stream completes (reply contains the tutor rule text)
await page.waitForFunction(
  () => {
    const bubbles = [...document.querySelectorAll(".bubble")];
    const last = bubbles[bubbles.length - 1];
    return last?.textContent.includes("quiz through it together");
  },
  { timeout: 10000 },
);
const after = await page.locator(".bubble").count();
console.log("bubbles:", before, "->", after);

// switch to Fractions tutoring, then new chat
await page.click("text=Fractions tutoring");
await page.click('button:has-text("New chat")');
await page.waitForTimeout(300);
const titles = await page.locator(".chat-list-item span").allTextContents();
console.log("chats:", titles.join(", "));

// search filter
await page.fill('input[aria-label="Search chats"]', "sky");
await page.waitForTimeout(200);
const filtered = await page.locator(".chat-list-item").count();
console.log("filtered rows for 'sky':", filtered);

// theme toggle light->dark
await page.click('[aria-label="Dark mode"]');
const theme = await page.getAttribute("html", "data-theme");
console.log("theme:", theme);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();
