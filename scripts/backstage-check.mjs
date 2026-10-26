import { chromium } from "playwright";

// Checks Backstage: the account-menu link, the /backstage page and its four
// toys, saved state, and the phone layout. Needs `npm run build` then
// `npm run preview` on :4173.
const BASE = "http://localhost:4173";
const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}: ${name}`, detail);
  if (!ok) failures += 1;
}

const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function waitUntil(page, fn, timeout = 4000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await fn()) return true;
    await page.waitForTimeout(80);
  }
  return false;
}

async function newPage(viewport = { width: 1280, height: 900 }) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    try {
      localStorage.setItem("cappuccina-lang", "en");
    } catch {}
  });
  return { page, errors };
}

// ---------- the account menu links to Backstage ----------
{
  const { page, errors } = await newPage();
  await page.route("**/api/v1/auth/me", (route) => route.fulfill(json({ id: "check", email: "check@example.com", displayName: "Check" })));
  await page.route("**/api/v1/chats", (route) => route.fulfill(json([])));
  await page.route("**/api/v1/memories**", (route) => route.fulfill(json([])));
  await page.goto(`${BASE}/`);
  await page.focus(".user-hint");
  await page.waitForSelector(".user-menu");
  const items = await page.$$eval(".user-menu-item", (els) => els.map((el) => el.textContent.trim()));
  check("menu order is Settings, Backstage, Log out", JSON.stringify(items) === JSON.stringify(["Settings", "Backstage", "Log out"]), items);
  await page.click('.user-menu-item:has-text("Backstage")');
  check("menu link opens /backstage/", await waitUntil(page, () => new URL(page.url()).pathname === "/backstage/"), page.url());
  check("Backstage tab title", await waitUntil(page, async () => (await page.title()).startsWith("Backstage")), await page.title());
  check("no errors in app or page", errors.length === 0, errors);
  await page.close();
}

// ---------- page, tabs and toys ----------
{
  const { page, errors } = await newPage();
  await page.goto(`${BASE}/backstage`);
  check("/backstage redirects to /backstage/", new URL(page.url()).pathname === "/backstage/", page.url());
  check("opens on the Pirouette toy", await page.isVisible(".pirouette"));

  // Pirouette: a tap is one full turn.
  const count = () => page.textContent(".pirouette .toy-count");
  await page.click(".pir-dancer");
  check("a tap spins one pirouette", await waitUntil(page, async () => (await count()).includes("1"), 5000), await count());

  // Tabs live in the hash; reload and back keep the toy.
  await page.click("#tab-bubbles");
  check("Foam tab sets #bubbles", new URL(page.url()).hash === "#bubbles");
  await page.click("#tab-latte");
  await page.goBack();
  check("back returns to Foam", await waitUntil(page, () => page.isVisible(".bubbles")));
  await page.reload();
  check("reload keeps Foam", await page.isVisible(".bubbles"));

  // Foam: tap pops one; a drag pops a whole row.
  await page.click('[data-bubble="0"]');
  check("tap pops a bubble", await page.getAttribute('[data-bubble="0"]', "aria-disabled") === "true");
  const first = await page.locator('[data-bubble="8"]').boundingBox();
  const last = await page.locator('[data-bubble="15"]').boundingBox();
  const y = first.y + first.height / 2;
  await page.mouse.move(first.x + 4, y);
  await page.mouse.down();
  await page.mouse.move(last.x + last.width - 4, y, { steps: 3 });
  await page.mouse.up();
  const row = await page.$$eval(".foam-row:nth-child(2) .foam-bubble", (els) => els.filter((el) => el.classList.contains("is-popped")).length);
  check("a fast drag pops the whole row", row === 8, row);
  check("pop count shows 9", (await page.textContent(".bubbles .toy-count")).includes("9"));

  // Latte: holding in Pour mode puts milk in the middle of the cup.
  await page.click("#tab-latte");
  const canvas = page.locator(".latte-canvas");
  const box = await canvas.boundingBox();
  const centerColor = () => page.$eval(".latte-canvas", (c) => [...c.getContext("2d").getImageData(c.width / 2, c.height / 2, 1, 1).data].slice(0, 3));
  const before = await centerColor();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(500);
  await page.mouse.up();
  const after = await centerColor();
  check("pouring turns the middle milky", after[0] > 230 && after[0] > before[0] + 20, { before, after });
  await page.click('button:has-text("Surprise design")');
  check("a surprise design finishes", await waitUntil(page, () => page.isEnabled('button:has-text("Surprise design")'), 8000));

  // Beats: a square toggles, plays through the steps, and is saved.
  await page.click("#tab-beats");
  const cell = '.beat-row:nth-child(1) .beat-cell >> nth=0';
  const was = await page.getAttribute(cell, "aria-pressed");
  await page.click(cell);
  check("a square toggles", (await page.getAttribute(cell, "aria-pressed")) !== was);
  await page.click('button:has-text("Play")');
  const steps = new Set();
  for (let i = 0; i < 25; i++) {
    steps.add(await page.$$eval(".beat-row:nth-child(1) .beat-cell", (els) => els.findIndex((el) => el.classList.contains("is-now"))));
    await page.waitForTimeout(100);
  }
  check("the looper walks through the steps", [0, 1, 2, 3, 4, 5, 6, 7].every((s) => steps.has(s)), [...steps]);
  await page.click('button:has-text("Stop")');
  await page.reload();
  check("the beat is saved", (await page.getAttribute(cell, "aria-pressed")) !== was);

  // Calm is saved per device.
  await page.click('.bs-switch:has-text("Calm")');
  await page.reload();
  check("Calm stays on after reload", await page.isVisible(".backstage.is-calm"));
  check("no errors on the page", errors.length === 0, errors);
  await page.close();
}

// ---------- phone width ----------
{
  const { page, errors } = await newPage({ width: 375, height: 812 });
  for (const toy of ["spin", "bubbles", "latte", "beats"]) {
    await page.goto(`${BASE}/backstage/#${toy}`);
    await page.waitForSelector(".toy");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(`no sideways scroll on ${toy} at 375px`, overflow <= 0, overflow);
  }
  const tabsFit = await page.$$eval(".bs-tab", (els) => els.every((el) => el.getBoundingClientRect().right <= window.innerWidth));
  check("all four tabs fit at 375px", tabsFit);
  check("no errors at phone width", errors.length === 0, errors);
  await page.close();
}

await browser.close();
console.log(failures ? `\n${failures} check(s) failed` : "\nAll Backstage checks passed");
process.exit(failures ? 1 : 0);
