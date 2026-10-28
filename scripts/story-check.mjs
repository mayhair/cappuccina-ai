import { chromium } from "playwright";

// Checks story mode end to end with the session, chats, lore book, character
// library, chapters and stream mocked: worlds, scene starters and cast, the
// request payload, suggested-action buttons, the OOC toggle and the "Story so
// far" panel. Needs `npm run preview` on :4173.
const BASE = "http://localhost:4173";
const browser = await chromium.launch();

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}: ${name}`, detail);
  if (!ok) failures += 1;
}

const json = (body, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const CHARACTERS = [
  { id: "espressona-signora", name: "Espressona Signora", blurb: "Her sister.", hasLook: true },
  { id: "cappuccino-assassino", name: "Cappuccino Assassino", blurb: "Retired ninja.", hasLook: false },
  { id: "tralalero-tralala", name: "Tralalero Tralala", blurb: "Shark in sneakers.", hasLook: true },
];

const chapters = [
  { id: 1, startUid: "x", endUid: null, text: "Tralalero owes us a race.", edited: true },
];

// Reply n: the third one answers out of character, as she does for "(ooc: …)".
const streamBody = (n) =>
  [
    "event: start\ndata: {}\n\n",
    `event: token\ndata: ${JSON.stringify({
      content: n === 3 ? "(ooc) Good question! Reply 3." : `The café is dark. Reply ${n}.`,
    })}\n\n`,
    `event: tool_call\ndata: ${JSON.stringify({ id: `t${n}`, name: "suggest_actions", display: "Next Moves", args: {} })}\n\n`,
    `event: tool_result\ndata: ${JSON.stringify({
      id: `t${n}`,
      name: "suggest_actions",
      ok: true,
      content: JSON.stringify({ status: "story_choices", options: ["Check the back room", "Call out to Espressona"] }),
    })}\n\n`,
    "event: done\ndata: {}\n\n",
  ].join("");

// One mocked app per scenario, opened on a new chat switched to Story mode.
async function openApp({ library = [] } = {}) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
  const app = { page, errors: [], bodies: [], created: null, library };
  page.on(
    "console",
    (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && app.errors.push(m.text()),
  );
  page.on("pageerror", (e) => app.errors.push(String(e)));
  await page.addInitScript(() => {
    try {
      localStorage.setItem("cappuccina-lang", "en");
    } catch {}
  });
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill(json({ id: "check", email: "check@example.com", displayName: "Check", storyChoices: true })),
  );
  await page.route("**/api/v1/chats", async (route) => {
    if (route.request().method() === "GET") return route.fulfill(json([]));
    app.created = route.request().postDataJSON();
    return route.fulfill(json({ id: "story-1", title: "New chat", mode: app.created.mode, story: app.created.story }));
  });
  await page.route("**/api/v1/chats/*/story/chapters", (route) =>
    route.fulfill(json({ chapters, maxChars: 4000, chapterSize: 30 })),
  );
  await page.route("**/api/v1/chats/*/active-branch", (route) => route.fulfill(json({ ok: true })));
  await page.route("**/api/v1/lore/characters", (route) => route.fulfill(json(CHARACTERS)));
  await page.route("**/api/v1/story/characters", async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill(json({ characters: app.library, limit: 100, nameMax: 80, textMax: 1500 }));
    }
    const row = { id: 100 + app.library.length, ...route.request().postDataJSON() };
    app.library.push(row);
    return route.fulfill(json(row, 201));
  });
  await page.route("**/api/v1/story/characters/*", async (route) => {
    const id = Number(route.request().url().split("/").pop());
    const row = app.library.find((c) => c.id === id);
    Object.assign(row, route.request().postDataJSON());
    return route.fulfill(json(row));
  });
  await page.route("**/api/v1/memories**", (route) => route.fulfill(json([])));
  await page.route("**/api/v1/chat/stream", (route) => {
    app.bodies.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: "text/event-stream", body: streamBody(app.bodies.length) });
  });
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click(".mode-chip");
  await page.click('.mode-option:has-text("Story")');
  await page.waitForSelector(".story-start", { timeout: 5000 });
  return app;
}

const replyShown = (page, n) =>
  page.waitForFunction(
    (n) => [...document.querySelectorAll(".bubble")].some((b) => b.textContent.includes(`Reply ${n}`)),
    n,
    { timeout: 5000 },
  );

// ---------- her world: the original flow ----------
{
  const app = await openApp();
  const { page, errors, bodies } = app;
  check("story start screen shown", true);
  check("mode chip says Story", (await page.textContent(".mode-chip")).includes("Story"));
  check(
    "her world is the default, with her in the scene",
    (await page.getAttribute('.story-world:has-text("Cappuccina’s world")', "aria-checked")) === "true" &&
      (await page.isChecked(".story-switch input")),
  );

  // Pick the café scene; its default cast is preselected, then add Tralalero.
  await page.click('.story-scene:has-text("Café after closing")');
  await page.waitForSelector('.story-chip:has-text("Tralalero")');
  await page.click('.story-chip:has-text("Tralalero")');
  const castOn = await page.$$eval(".story-chip.on", (els) => els.map((e) => e.textContent));
  check("cast picker toggles", castOn.length === 3, castOn.join(", "));
  await page.click(".story-go");

  await replyShown(page, 1);
  const first = bodies[0];
  check("request mode is story", first?.mode === "story");
  check(
    "request carries world, scenario and cast",
    first?.story?.world === "cappuccina" &&
      first?.story?.withCappuccina === true &&
      first?.story?.scenario?.includes("café is dark") &&
      first?.story?.cast?.includes("tralalero-tralala") &&
      first?.story?.cast?.includes("espressona-signora"),
    JSON.stringify(first?.story),
  );
  const { created } = app;
  check("chat created as a story chat", created?.mode === "story" && created?.story?.cast?.length === 3);
  check("opening line sent", first?.messages?.at(-1)?.content?.startsWith("Let’s start the story"));
  check("story bar names the world", (await page.textContent(".story-bar-world")).includes("Cappuccina’s world"));

  // Suggested actions show under the newest reply; a click sends one.
  await page.waitForSelector(".story-choice", { timeout: 5000 });
  const options = await page.$$eval(".story-choice", (els) => els.map((e) => e.textContent));
  check("choice buttons rendered", options.length === 2, options.join(" | "));
  await page.click('.story-choice:has-text("Check the back room")');
  await replyShown(page, 2);
  check("choice sent as the user's message", bodies[1]?.messages?.at(-1)?.content === "Check the back room");
  check("only the newest reply has choices", (await page.$$(".story-choices")).length === 1);

  // OOC toggle wraps the next message and marks it, and her (ooc) reply too.
  await page.click(".story-ooc");
  check(
    "OOC placeholder",
    (await page.getAttribute('textarea[aria-label="Message Cappuccina"]', "placeholder")).startsWith("Out of character"),
  );
  await page.fill('textarea[aria-label="Message Cappuccina"]', "is Espressona based on someone?");
  await page.keyboard.press("Enter");
  await replyShown(page, 3);
  const oocText = bodies[2]?.messages?.at(-1)?.content;
  check("OOC message wrapped", oocText === "(ooc: is Espressona based on someone?)", oocText);
  check("OOC user row styled", (await page.$$(".msg-row.user.ooc")).length === 1);
  check("OOC reply row styled", (await page.$$(".msg-row.assistant.ooc")).length === 1);
  check("OOC toggle resets after sending", (await page.getAttribute(".story-ooc", "aria-pressed")) === "false");

  // Sidebar tag and the "Story so far" panel.
  check("STORY tag in sidebar", (await page.$$(".chat-tag.story")).length >= 1);
  await page.click('.story-bar-btn:has-text("Story so far")');
  await page.waitForSelector(".story-chapters .memory-row", { timeout: 5000 });
  check(
    "hand-written note listed",
    (await page.textContent(".story-chapters")).includes("Tralalero owes us a race."),
  );
  await page.click(".story-chapters .modal-x");
  check("no console errors (her world)", errors.length === 0, errors.join("\n"));
  await page.close();
}

// ---------- another world, Cappuccina as narrator, a saved character ----------
{
  const { page, errors, bodies } = await openApp({
    library: [{ id: 100, name: "Aria", description: "An elf ranger who trusts no one", voice: "" }],
  });
  await page.click('.story-world:has-text("Fantasy")');
  check("fantasy scenes shown", (await page.$$('.story-scene:has-text("A stranger at the tavern")')).length === 1);
  check("Cappuccina defaults to narrator outside her world", !(await page.isChecked(".story-switch input")));
  check(
    "her world's cast is tucked away outside it",
    (await page.$$('.story-chip:has-text("Tralalero")')).length === 0 &&
      (await page.getAttribute(".story-disclosure", "aria-expanded")) === "false",
  );
  check("hint says she narrates", (await page.textContent(".story-switch-hint")).includes("narrator"));

  await page.click('.story-scene:has-text("The dragon’s bargain")');
  await page.fill('input[aria-label="Who you play"]', "A nervous young knight");
  check("no inline character form in the picker", (await page.$$(".story-cast-editor .character-form")).length === 0);
  await page.click('.story-chip:has-text("Aria")');
  check("saved character cast", (await page.$$('.story-chip.on:has-text("Aria")')).length === 1);

  await page.click(".story-go");
  await replyShown(page, 1);
  const story = bodies[0]?.story;
  check(
    "request carries the fantasy setup",
    story?.world === "fantasy" &&
      story?.withCappuccina === false &&
      story?.scenario?.includes("dragon") &&
      story?.player === "A nervous young knight" &&
      story?.cast?.length === 0 &&
      JSON.stringify(story?.characters) === "[100]",
    JSON.stringify(story),
  );
  check("story bar names the world", (await page.textContent(".story-bar-world")).includes("Fantasy"));
  check("no console errors (fantasy)", errors.length === 0, errors.join("\n"));
  await page.close();
}

// ---------- the cast picker links to the library in Settings ----------
{
  const app = await openApp({ library: [{ id: 100, name: "Aria", description: "", voice: "" }] });
  const { page, errors } = app;
  await page.click('.story-world:has-text("Fantasy")');
  await page.waitForSelector('.story-chip:has-text("Aria")', { timeout: 5000 });
  await page.click(".story-manage");
  await page.waitForSelector(".character-row", { timeout: 5000 });
  check("manage link opens the character library", (await page.textContent(".character-row")).includes("Aria"));
  await page.click('.character-row button[aria-label="Edit Aria"]');
  await page.fill('.character-row input[maxlength="80"]', "Aria the Bold");
  await page.click('.character-row .settings-save');
  await page.waitForSelector('.character-row:has-text("Aria the Bold")', { timeout: 5000 });
  await page.keyboard.press("Escape");
  await page.waitForSelector(".character-row", { state: "detached", timeout: 5000 });
  check(
    "cast picker shows the edit made in Settings",
    (await page.$$('.story-chip:has-text("Aria the Bold")')).length === 1,
  );
  check("no console errors (library link)", errors.length === 0, errors.join("\n"));
  await page.close();
}

// ---------- a fandom needs its description before it can begin ----------
{
  const { page, errors, bodies } = await openApp();
  await page.click('.story-world:has-text("Fandom")');
  check("fandom begin disabled until described", await page.isDisabled(".story-go"));
  await page.fill('textarea[aria-label="Fandom"]', "Pokémon, in Kanto");
  check("fandom begin enabled once described", !(await page.isDisabled(".story-go")));
  await page.click(".story-go");
  await replyShown(page, 1);
  const story = bodies[0]?.story;
  check(
    "request carries the fandom",
    story?.world === "fandom" && story?.worldText === "Pokémon, in Kanto",
    JSON.stringify(story),
  );
  check("no console errors (fandom)", errors.length === 0, errors.join("\n"));
  await page.close();
}

await browser.close();
console.log(failures ? `${failures} check(s) failed` : "all story checks passed");
process.exit(failures ? 1 : 0);
