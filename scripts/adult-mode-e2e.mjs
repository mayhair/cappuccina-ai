import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

// mutable server state: the flag lives on the user row, so a PATCH is visible
// on the next GET (this is what proves persistence across a reload)
let user = { id: "check", email: "check@example.com", displayName: "Check", adultMode: false, adultModeEnabled: true };
let patched = null;
await page.route("**/api/v1/auth/me", async (route) => {
  if (route.request().method() === "PATCH") {
    patched = route.request().postDataJSON();
    user = { ...user, ...patched };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(user),
    });
  } else {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(user),
    });
  }
});
await page.route("**/api/v1/chats", async (route) => {
  await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
});

await page.goto("http://localhost:4173/");
await page.focus('[aria-label="Account options"]');
await page.click("text=Settings");

// open the Adult mode section
await page.click(".settings-nav-item:has-text('Adult mode')");
await page.waitForSelector("#settings-birthday", { timeout: 5000 });

// an under-18 date must be rejected and leave the toggle off
await page.fill("#settings-birthday", "2016-01-01");
await page.click("text=Enable adult mode");
await page.waitForSelector(".settings-msg.error", { timeout: 3000 });
const rejected = await page.locator(".settings-msg.error").textContent();
console.log("under-18 rejected:", rejected.includes("18 or older"));
console.log("no PATCH sent for under-18:", patched === null);
const stillOff = (await page.locator("text=Enable adult mode").count()) === 1;
console.log("still off after under-18:", stillOff);

// a valid 18+ birthday turns it on and PATCHes the server
await page.fill("#settings-birthday", "2000-01-01");
await page.click("text=Enable adult mode");
await page.waitForSelector("text=Adult mode is on", { timeout: 5000 });
console.log("PATCH body:", JSON.stringify(patched));
const patchOk = patched && patched.adultMode === true && Object.keys(patched).length === 1;
console.log("PATCH carries only adultMode:true:", patchOk);

// persistence: a fresh load reads the flag back from the server
await page.reload();
await page.focus('[aria-label="Account options"]');
await page.click("text=Settings");
await page.click(".settings-nav-item:has-text('Adult mode')");
await page.waitForSelector("text=Adult mode is on", { timeout: 5000 });
console.log("still on after reload:", (await page.locator("text=Adult mode is on").count()) === 1);

// turning it off works and PATCHes false
await page.click("text=Turn off adult mode");
await page.waitForSelector("#settings-birthday", { timeout: 5000 });
console.log("PATCH body on turn-off:", JSON.stringify(patched));
const offOk = patched && patched.adultMode === false;
console.log("turned off:", offOk);

console.log("console errors:", errors.length ? errors : "none");
await browser.close();

if (!rejected.includes("18 or older") || !stillOff || !patchOk || !offOk) process.exit(1);
