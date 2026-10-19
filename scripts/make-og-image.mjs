// Renders the link-preview card (Open Graph / Twitter / Discord embed) into
// assets/og-image.png at 1200x630.
// Run with: node scripts/make-og-image.mjs
// The card is laid out as HTML in headless Chromium (the playwright
// devDependency) so it uses the app's own fonts, colours and plates.
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const outPath = join(root, 'assets', 'og-image.png')

async function dataUrl(rel, type) {
  const bytes = await readFile(join(root, rel))
  return `data:${type};base64,${bytes.toString('base64')}`
}

const sky = await dataUrl('assets/plates/sky-band.webp', 'image/webp')
const hero = await dataUrl('assets/plates/welcome-hero.webp', 'image/webp')

const html = `<!doctype html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Jua&family=Cabin:wght@500;600;700&display=block" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; }
  html, body { width: 1200px; height: 630px; overflow: hidden; }
  body {
    position: relative;
    font-family: 'Cabin', system-ui, sans-serif;
    color: #2c3151;
    background: #eef0fb url('${sky}') center 30% / cover no-repeat;
  }
  /* Pink-to-lilac wash so the white clouds read as the app's palette. */
  .wash {
    position: absolute; inset: 0;
    background:
      radial-gradient(620px 520px at 905px 360px, rgba(243, 143, 193, 0.42), transparent 70%),
      radial-gradient(520px 420px at 120px 600px, rgba(183, 155, 232, 0.30), transparent 72%),
      linear-gradient(100deg, rgba(238, 240, 251, 0.70) 0%, rgba(238, 240, 251, 0.25) 55%, rgba(248, 215, 236, 0.10) 100%);
  }
  .sparkle { position: absolute; color: #fff; filter: drop-shadow(0 2px 6px rgba(234, 114, 178, 0.55)); }
  .sparkle svg { display: block; }

  .hero {
    position: absolute;
    right: 0; bottom: -78px;
    width: 610px;
    filter: drop-shadow(0 24px 40px rgba(64, 62, 96, 0.22));
  }

  .copy {
    position: absolute;
    left: 76px; top: 0; bottom: 0;
    width: 600px;
    display: flex; flex-direction: column; justify-content: center;
  }
  .chip {
    align-self: flex-start;
    display: inline-flex; align-items: center; gap: 10px;
    padding: 9px 18px 9px 14px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.78);
    border: 1.5px solid rgba(234, 114, 178, 0.40);
    color: #b02e74;
    font-weight: 700; font-size: 22px; letter-spacing: 0.01em;
    box-shadow: 0 6px 16px rgba(64, 62, 96, 0.10);
  }
  .chip .dot { width: 12px; height: 12px; border-radius: 50%; background: #ea72b2; box-shadow: 0 0 0 4px rgba(234, 114, 178, 0.22); }
  h1 {
    margin-top: 26px;
    font-family: 'Jua', 'Cabin', sans-serif;
    font-weight: 400;
    font-size: 92px; line-height: 0.98; letter-spacing: -0.02em;
    color: #2c3151;
  }
  h1 .pink {
    display: block;
    background: linear-gradient(95deg, #ea72b2 0%, #d863b0 45%, #9b7be0 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
    padding-bottom: 6px;
  }
  .sub {
    margin-top: 22px;
    font-size: 31px; line-height: 1.3; font-weight: 500;
    color: #3e4468;
    max-width: 520px;
  }
  .pills { margin-top: 34px; display: flex; gap: 12px; }
  .pill {
    display: inline-flex; align-items: center; gap: 9px;
    padding: 11px 20px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid rgba(44, 49, 81, 0.14);
    font-size: 22px; font-weight: 600; color: #3e4468;
    box-shadow: 0 4px 12px rgba(64, 62, 96, 0.08);
  }
  .pill svg { width: 22px; height: 22px; color: #ea72b2; }
  .url {
    position: absolute; left: 76px; bottom: 40px;
    font-size: 22px; font-weight: 600; letter-spacing: 0.02em;
    color: #636983;
  }
  .url b { color: #b02e74; font-weight: 700; }
</style></head>
<body>
  <div class="wash"></div>

  <span class="sparkle" style="left:610px; top:96px"><svg width="34" height="34" viewBox="0 0 24 24"><path fill="currentColor" d="M12 0c.6 6.2 5.8 11.4 12 12-6.2.6-11.4 5.8-12 12-.6-6.2-5.8-11.4-12-12C6.2 11.4 11.4 6.2 12 0z"/></svg></span>
  <span class="sparkle" style="left:1120px; top:120px"><svg width="22" height="22" viewBox="0 0 24 24"><path fill="currentColor" d="M12 0c.6 6.2 5.8 11.4 12 12-6.2.6-11.4 5.8-12 12-.6-6.2-5.8-11.4-12-12C6.2 11.4 11.4 6.2 12 0z"/></svg></span>
  <span class="sparkle" style="left:700px; top:470px"><svg width="20" height="20" viewBox="0 0 24 24"><path fill="currentColor" d="M12 0c.6 6.2 5.8 11.4 12 12-6.2.6-11.4 5.8-12 12-.6-6.2-5.8-11.4-12-12C6.2 11.4 11.4 6.2 12 0z"/></svg></span>

  <img class="hero" src="${hero}" alt="">

  <div class="copy">
    <span class="chip"><span class="dot"></span>Your AI chat friend</span>
    <h1>Ballerina<span class="pink">Cappuccina</span></h1>
    <p class="sub">Ask me anything, or let’s learn together.</p>
    <div class="pills">
      <span class="pill"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>Just talking</span>
      <span class="pill"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11v5c3 2 9 2 12 0v-5"/></svg>Homework help</span>
    </div>
  </div>

  <div class="url"><b>cappuccina</b>.mayhair.net</div>
</body></html>`

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 })
await page.setContent(html, { waitUntil: 'networkidle' })
await page.evaluate(() => document.fonts.ready)
await page.screenshot({ path: outPath, type: 'png' })
await browser.close()
console.log(`Wrote ${outPath}`)
