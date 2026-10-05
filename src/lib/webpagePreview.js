// Assemble a chat's webpage files into one self-contained HTML document for the
// sandboxed preview iframe. Dependency-free, like lib/markdown: local <link>
// and <script> references are inlined by exact filename, while external URLs
// (CDNs, data URIs) are left for the browser to fetch. The iframe renders from
// srcdoc, so relative URLs have nothing to resolve against — inlining is what
// makes a multi-file page work without a server.

const ENTRY_CANDIDATES = ['index.html', 'index.htm']

// An external reference (http(s), protocol-relative, data:, or a fragment) is
// never inlined — only names that match a file in the environment are.
function isExternal(url) {
  return /^(https?:)?\/\//i.test(url) || /^(data:|blob:|#|mailto:)/i.test(url)
}

function localName(url) {
  if (isExternal(url)) return null
  try {
    return decodeURIComponent(url.split(/[?#]/)[0])
  } catch {
    return url.split(/[?#]/)[0]
  }
}

function inlineLinks(html, files) {
  return html.replace(
    /<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*>/gi,
    (tag) => {
      const href = tag.match(/\bhref\s*=\s*["']([^"']+)["']/i)?.[1]
      const name = href ? localName(href) : null
      if (!name || !(name in files)) return tag
      return `<style>\n${files[name]}\n</style>`
    },
  )
}

function inlineScripts(html, files) {
  return html.replace(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>\s*<\/script>/gi, (tag, src) => {
    const name = localName(src)
    if (!name || !(name in files)) return tag
    const module = /\btype\s*=\s*["']module["']/i.test(tag)
    return `<script${module ? ' type="module"' : ''}>\n${files[name]}\n</script>`
  })
}

export function pickEntryFile(files) {
  for (const name of ENTRY_CANDIDATES) {
    if (name in files) return name
  }
  const html = Object.keys(files).find((name) => /\.html?$/i.test(name))
  return html ?? null
}

export function hasFiles(files) {
  return Boolean(files && Object.keys(files).length > 0)
}

// The preview document, ready for iframe srcDoc. Returns '' when there is
// nothing to show; callers render an empty state instead.
export function assemblePreview(files) {
  if (!hasFiles(files)) return ''
  const entry = pickEntryFile(files)
  if (!entry) {
    // No HTML entry: show the files as a simple list so the canvas isn't blank.
    const names = Object.keys(files).sort()
    return `<!doctype html><meta charset="utf-8"><body style="font-family:sans-serif;padding:2rem"><h1>This page has no index.html</h1><ul>${names
      .map((n) => `<li>${n.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c])}</li>`)
      .join('')}</ul></body>`
  }
  const html = inlineScripts(inlineLinks(files[entry] ?? '', files), files)
  // A filename the model chose won't match a regex; guard against an empty doc.
  return html.trim() ? html : ''
}
