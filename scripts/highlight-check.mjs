// Unit check for the Prism adapter. Confirms each surface (canvas files,
// tool-arg JSON, markdown fences) emits the expected Prism token classes,
// escapes untrusted markup, survives edge cases, and that every class the
// bundled grammars emit has a rule in src/styles.css.
// Run with: node scripts/highlight-check.mjs
import fs from 'node:fs'
import { highlightCode, highlightJson, highlightFence } from '../src/lib/highlightCode.js'

const failures = []
const check = (label, condition) => {
  if (!condition) failures.push(label)
  console.log(`${condition ? 'ok  ' : 'FAIL'} ${label}`)
}

// The class names on every <span> in a highlighted fragment.
const classes = (html) => {
  const out = new Set()
  for (const m of html.matchAll(/class="([^"]+)"/g)) for (const c of m[1].split(/\s+/)) out.add(c)
  return out
}
const has = (html, cls) => classes(html).has(cls)

const html = highlightCode('<h1 class="hero">Hi &amp; bye</h1>', 'index.html')
check('html: tag token', has(html, 'tag'))
check('html: attribute name token', has(html, 'attr-name'))
check('html: attribute value token', has(html, 'attr-value'))
check('html: entity token', has(html, 'entity'))
check('html: markup is escaped', html.includes('&lt;') && !html.includes('<h1'))

const inlineHtml = highlightCode('<style>.a{color:red}</style><script>const q=1;</script>', 'x.html')
check('html: inline <script> is highlighted', has(inlineHtml, 'language-javascript') && has(inlineHtml, 'keyword'))
check('html: inline <style> is highlighted', has(inlineHtml, 'language-css') && has(inlineHtml, 'selector'))

const cssOut = highlightCode('body { background: rgb(1, 2, 3); }', 'styles.css')
check('css: selector token', has(cssOut, 'selector'))
check('css: property token', has(cssOut, 'property'))
check('css: function token', has(cssOut, 'function'))
check('css: punctuation token (misc text)', has(cssOut, 'punctuation'))

const js = highlightCode("const heading = 'hi';\nfunction label(text) { return text; }", 'script.js')
check('js: keyword token', has(js, 'keyword'))
check('js: string token', has(js, 'string'))
check('js: function name token', has(js, 'function'))
check('js: parameter token', has(js, 'parameter'))
check('js: operator token (misc text)', has(js, 'operator'))
check('js: variable identifiers stay plain text', !/<span[^>]*>heading</.test(js) && js.includes('heading'))

const richJs = highlightCode(
  'class C { m() { return this.value; } }\nconst o = { a: 1, b: 2 };\nconst s = `hi ${name}`;',
  'x.js',
)
check('js: property token', has(richJs, 'property'))
check('js: template interpolation token', has(richJs, 'interpolation'))
check('js: regex token', has(highlightCode('const re = /ab+c/gi;', 'x.js'), 'regex'))

const json = highlightJson('{"draft_id": 7, "sent": true}')
check('json: key token', has(json, 'property'))
check('json: number token', has(json, 'number'))
check('json: boolean token', has(json, 'boolean'))
check('json: punctuation token', has(json, 'punctuation'))
const jsonPlain = highlightJson('plain <b>text</b>')
check('json: non-JSON text is escaped, not thrown', jsonPlain.includes('&lt;b') && !jsonPlain.includes('<b>'))

const fenced = highlightFence('js', 'const x = 1')
check('fence(js): keyword token', has(fenced, 'keyword'))
const plain = highlightFence('python', 'print("<hi>")')
check('fence(unknown language) falls back to escaped plaintext', !plain.includes('token ') && plain.includes('&lt;hi>'))
check('fence with no language escapes and does not crash', highlightFence('', 'const x = 1') === 'const x = 1')

check('null/empty input is safe', highlightCode(null, null) === '' && highlightCode('', 'x.css') === '')
check('partial/malformed source does not throw', typeof highlightCode('function broken(', 'script.js') === 'string')

// File extension and fence alias resolution.
check('extension is case-insensitive (.CSS)', has(highlightCode('a{b:c}', 'STYLES.CSS'), 'selector'))
check('extension .mjs -> javascript', has(highlightCode('const x = 1', 'app.mjs'), 'keyword'))
check('extension .svg -> markup', has(highlightCode('<a/>', 'pic.svg'), 'tag'))
check('fence alias is case-insensitive (TypeScript)', has(highlightFence('TypeScript', 'const x: number = 1'), 'keyword'))
check('fence alias scss -> css does not crash', typeof highlightFence('scss', '$x:1;.a{color:$x}') === 'string')

// Every token class the bundled grammars can emit must have a CSS rule, or it
// would render unstyled. Samples intentionally exercise at-rules, pseudo
// selectors, attribute selectors, custom properties, jsdoc/labels, regexes,
// template interpolation, inlined <script>/<style>, entities and CDATA.
const broadHtml = highlightCode(
  '<!doctype html>\n<!-- c -->\n<h1 class="hero" style="color:red" onclick="f()" hidden>Hi &amp; bye</h1>\n<svg:rect/>\n<style>body{color:red}</style>\n<script type="module">const q=1;</script>',
  'x.html',
)
const broadCss = highlightCode(
  '@media (min-width:10px){.a::before,#id,.cls[attr]:hover,div:not(.x){color:var(--brand)!important;background:url(x.png);content:"x"}}',
  'x.css',
)
const broadJs = highlightCode(
  '/** @param */\nlabel: for (const k of [1]) { if (k) continue label; }\nconst re = /ab+c/gi;\nconst s = `v=${k}`;\nclass Box { #n = 1; }\nconst C = MY_CONST, b = true, n = null;',
  'x.js',
)
const stylesheet = fs.readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8')
const styled = new Set([...stylesheet.matchAll(/\.token\.([a-zA-Z0-9_-]+)/g)].map((m) => m[1]))
const emitted = new Set()
for (const out of [html, inlineHtml, cssOut, js, richJs, json, broadHtml, broadCss, broadJs]) {
  for (const c of classes(out)) if (c !== 'token') emitted.add(c)
}
const unstyled = [...emitted].filter((c) => !styled.has(c))
check(`all emitted token classes are styled (${emitted.size} classes)`, unstyled.length === 0)
if (unstyled.length) console.log('unstyled:', unstyled.join(', '))

// A generated page can be large; highlighting runs on every canvas render, so
// keep it comfortably interactive.
const big = Array.from({ length: 4000 }, (_, i) => `const v${i} = ${i};`).join('\n')
const started = performance.now()
const bigOut = highlightCode(big, 'big.js')
const ms = performance.now() - started
check(`large input (${Math.round(big.length / 1024)}KB) highlights under 1s (${ms.toFixed(0)}ms)`, bigOut.length > 0 && ms < 1000)

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`)
  process.exit(1)
}
console.log('\nall highlight checks passed')
