import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const fam = process.argv[2] || "Cabin";

// Inline the self-hosted woff2 faces for this family (weight 400) as data URIs,
// since setContent runs on about:blank and cannot load files from disk.
const fontsDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src");
const faces = readFileSync(path.join(fontsDir, "fonts.css"), "utf8")
  .split("@font-face")
  .slice(1)
  .filter((b) => b.includes(`font-family: '${fam}'`) && /font-weight: 400;/.test(b))
  .map((b) =>
    b.replace(/url\(\.\/fonts\/([^)]+)\)/, (_, file) => {
      const data = readFileSync(path.join(fontsDir, "fonts", file)).toString("base64");
      return `url(data:font/woff2;base64,${data})`;
    })
  )
  .map((b) => `@font-face${b}`)
  .join("\n");
const sizes = [22, 23, 24, 25, 26, 27];
const S = {
  A: "Ciao! I'm Cappuccina \u2014 an AI who loves",
  "A+": "Ciao! I'm Cappuccina \u2014 an AI who loves big",
  B: "big questions. What's on your mind today?",
  C: "I have a science test tomorrow and",
  "C+": "I have a science test tomorrow and I'm",
  D: "I'm kinda nervous",
  E: "You've got this! \u2764\ufe0f\ud83d\udc95",
  "E+": "You've got this! \u2764\ufe0f\ud83d\udc95 I'm",
  F: "I'm here for you \u2014 want to quiz",
  "F+": "I'm here for you \u2014 want to quiz through",
  G: "through it together?",
};
const keys = Object.keys(S);

const browser = await chromium.launch();
const page = await browser.newPage();
await page.setContent(`<style>${faces}</style><body></body>`);
await page.waitForTimeout(1200);
const out = await page.evaluate(
  async ([fam, sizes, S, keys]) => {
    const c = document.createElement("canvas").getContext("2d");
    await document.fonts.load(`400 24px "${fam}"`);
    const rows = [];
    for (const sz of sizes) {
      c.font = `400 ${sz}px "${fam}"`;
      const w = {};
      for (const k of keys) w[k] = Math.round(c.measureText(S[k]).width);
      rows.push({ sz, w });
    }
    return rows;
  },
  [fam, sizes, S, keys]
);
console.log("font:", fam);
for (const { sz, w } of out) {
  const c1lo = Math.max(w.A, w.B), c1hi = w["A+"];
  const ulo = Math.max(w.C, w.D), uhi = w["C+"];
  const c3lo = Math.max(w.E, w.F, w.G), c3hi = Math.min(w["E+"], w["F+"]);
  const ok = (lo, hi) => (lo < hi ? `${lo}-${hi - 1}` : "IMPOSSIBLE");
  console.log(
    `  ${sz}px  c1(${ok(c1lo, c1hi)})  user(${ok(ulo, uhi)})  c3(${ok(c3lo, c3hi)})  [A=${w.A} A+=${w["A+"]} B=${w.B} C=${w.C} C+=${w["C+"]} E=${w.E} E+=${w["E+"]} F=${w.F} F+=${w["F+"]}]`
  );
}
await browser.close();
