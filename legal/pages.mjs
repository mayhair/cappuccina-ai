// Renders the Terms of Service and Privacy Policy as standalone HTML pages at
// /terms and /privacy. They are plain static pages, not part of the React app,
// so they load without signing in or running JavaScript (Google's OAuth review
// and crawlers need that) and the alpha notice never covers them.
import { readFileSync } from 'node:fs'
import { marked } from 'marked'

const PAGES = {
  terms: { file: 'terms-of-service.md', title: 'Terms of Service' },
  privacy: { file: 'privacy-policy.md', title: 'Privacy Policy' },
}

// Wraps a table for sideways scrolling and gives each cell its column name,
// which the phone layout shows above the cell.
function labelCells(table) {
  const heads = [...table.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map((m) => m[1].replace(/<[^>]+>|"/g, ''))
  const rows = table.replace(/<tr>([\s\S]*?)<\/tr>/g, (_, cells) => {
    let i = 0
    return `<tr>${cells.replace(/<td/g, () => `<td data-label="${heads[i++] ?? ''}"`)}</tr>`
  })
  return `<div class="table">${rows}</div>`
}

function render(slug) {
  const { file, title } = PAGES[slug]
  const md = readFileSync(new URL(file, import.meta.url), 'utf8')
  const body = marked
    .parse(md)
    // Links between the documents stay on whichever host serves them (dev or live).
    .replace(/href="https:\/\/cappuccina\.mayhair\.net\/(terms|privacy)"/g, 'href="/$1"')
    // Every other link leaves the site, so open it in a new tab.
    .replace(/<a href="(https?:[^"]+)"/g, '<a href="$1" target="_blank" rel="noopener noreferrer"')
  const other = slug === 'terms' ? 'privacy' : 'terms'

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="color-scheme" content="light dark" />
<title>${title} · Ballerina Cappuccina</title>
<link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192.png" />
<script>
  // Follow the theme chosen in the app, if any; otherwise the CSS follows the OS.
  try {
    const t = localStorage.getItem('cappuccina-theme')
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t
  } catch {}
</script>
<style>
  :root {
    --ink: #2c3151; --ink-soft: #3e4468; --ink-faint: #636983;
    --pink-ink: #b02e74; --hairline: rgba(44, 49, 81, 0.18);
    --page-bg: #eef0fb; --card: #ffffff; --soak: rgba(234, 114, 178, 0.1);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme='light']) {
      --ink: #eef0f6; --ink-soft: #b9bfd4; --ink-faint: #969cb6;
      --pink-ink: #ffa8d6; --hairline: rgba(238, 240, 246, 0.16);
      --page-bg: #10162a; --card: #171e35; --soak: rgba(246, 199, 222, 0.08);
    }
  }
  :root[data-theme='dark'] {
    --ink: #eef0f6; --ink-soft: #b9bfd4; --ink-faint: #969cb6;
    --pink-ink: #ffa8d6; --hairline: rgba(238, 240, 246, 0.16);
    --page-bg: #10162a; --card: #171e35; --soak: rgba(246, 199, 222, 0.08);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--page-bg); color: var(--ink-soft);
    font: 16px/1.65 'Cabin', 'Nunito', system-ui, sans-serif;
  }
  .wrap { max-width: 760px; margin: 0 auto; padding: 1.25rem 16px 4rem; }
  nav { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; font-weight: 700; font-size: 14.5px; }
  nav a { color: var(--ink-soft); text-decoration: none; }
  nav a:hover { color: var(--pink-ink); }
  article {
    margin-top: 1.25rem; padding: clamp(1.25rem, 4vw, 2.5rem);
    background: var(--card); border: 1px solid var(--hairline); border-radius: 18px;
  }
  h1 { margin: 0 0 0.75rem; color: var(--ink); font-size: clamp(26px, 5vw, 34px); line-height: 1.2; }
  h2 { margin: 2.2rem 0 0.6rem; color: var(--ink); font-size: 21px; line-height: 1.3; }
  h3 { margin: 1.5rem 0 0.4rem; color: var(--ink); font-size: 17px; }
  strong { color: var(--ink); }
  a { color: var(--pink-ink); overflow-wrap: anywhere; }
  ul, ol { padding-inline-start: 1.4rem; }
  li { margin: 0.3rem 0; }
  .table { overflow-x: auto; margin: 1rem 0; }
  table { border-collapse: collapse; width: 100%; min-width: 560px; font-size: 14.5px; line-height: 1.5; }
  th, td { text-align: start; vertical-align: top; padding: 0.6rem 0.7rem; border-bottom: 1px solid var(--hairline); }
  th { color: var(--ink); background: var(--soak); }
  /* Phones: each row becomes a card, each cell labelled with its column. */
  @media (max-width: 600px) {
    table { min-width: 0; }
    thead { display: none; }
    tr { display: block; padding: 0.4rem 0; border-bottom: 1px solid var(--hairline); }
    td { display: block; padding: 0.25rem 0; border: 0; }
    td::before { content: attr(data-label); display: block; color: var(--ink-faint); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
  }
</style>
</head>
<body>
<div class="wrap">
  <nav>
    <a href="/">← Ballerina Cappuccina</a>
    <a href="/${other}">${PAGES[other].title}</a>
  </nav>
  <article>
${body.replace(/<table>[\s\S]*?<\/table>/g, labelCells)}
  </article>
</div>
</body>
</html>
`
}

// Vite plugin: serves the pages in dev and writes terms.html / privacy.html
// into the build (Cloudflare serves those at /terms and /privacy).
export function legalPages() {
  return {
    name: 'legal-pages',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const slug = req.url.split('?')[0].replace(/^\/|\/$/g, '')
        if (!Object.hasOwn(PAGES, slug)) return next()
        res.setHeader('Content-Type', 'text/html; charset=utf-8')
        res.end(render(slug))
      })
    },
    generateBundle() {
      for (const slug of Object.keys(PAGES)) {
        this.emitFile({ type: 'asset', fileName: `${slug}.html`, source: render(slug) })
      }
    },
  }
}
