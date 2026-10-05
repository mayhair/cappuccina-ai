// Unit check for the highlight.js adapter. Confirms each surface (canvas files,
// tool-arg JSON, markdown fences) emits the expected hljs token classes, escapes
// untrusted markup, survives edge cases, and that every class the bundled
// grammars emit has a rule in src/styles.css.
// Run with: node scripts/highlight-check.mjs
import fs from 'node:fs'
import { highlightCode, highlightJson, highlightFence } from '../src/lib/highlightCode.js'

const failures = []
const check = (label, condition) => {
  if (!condition) failures.push(label)
  console.log(`${condition ? 'ok  ' : 'FAIL'} ${label}`)
}

const html = highlightCode('<h1 class="hero">Hi &amp; bye</h1>', 'index.html')
check('html: tag name token', html.includes('hljs-name'))
check('html: attribute token', html.includes('hljs-attr'))
check('html: attribute value is a string token', html.includes('hljs-string'))
check('html: markup is escaped', html.includes('&lt;') && !html.includes('<h1'))

const inlineHtml = highlightCode('<style>.a{color:red}</style><script>const q=1;</script>', 'x.html')
check('html: inline <script> is highlighted', inlineHtml.includes('hljs-keyword'))
check('html: inline <style> is highlighted', /hljs-(selector|attribute)/.test(inlineHtml))

const cssOut = highlightCode('body { background: rgb(1, 2, 3); }', 'styles.css')
check('css: selector token', cssOut.includes('hljs-selector-tag'))
check('css: property token', cssOut.includes('hljs-attribute'))

const js = highlightCode("const heading = 'hi';\nfunction label(text) { return text; }", 'script.js')
check('js: keyword token', js.includes('hljs-keyword'))
check('js: string token', js.includes('hljs-string'))
check('js: function name token', js.includes('hljs-title'))
check('js: parameter token', js.includes('hljs-params'))

const richJs = highlightCode('class C { m() { return this.value; } }\nconst s = `hi ${name}`;', 'x.js')
check('js: property token', richJs.includes('hljs-property'))
check('js: template substitution token', richJs.includes('hljs-subst'))

const json = highlightJson('{"draft_id": 7, "sent": true}')
check('json: key token', json.includes('hljs-attr'))
check('json: number token', json.includes('hljs-number'))
check('json: literal token', json.includes('hljs-literal'))
check('json: non-JSON text is escaped, not thrown', highlightJson('plain <b>text</b>') === 'plain &lt;b&gt;text&lt;/b&gt;')

const fenced = highlightFence('js', 'const x = 1')
check('fence(js): keyword token', fenced.includes('hljs-keyword'))
const plain = highlightFence('python', 'print("<hi>")')
check('fence(unknown language) falls back to escaped plaintext', !plain.includes('hljs-') && plain.includes('&lt;hi&gt;'))
check('fence with no language escapes and does not crash', highlightFence('', 'const x = 1') === 'const x = 1')

check('null/empty input is safe', highlightCode(null, null) === '' && highlightCode('', 'x.css') === '')
check('partial/malformed source does not throw', typeof highlightCode('function broken(', 'script.js') === 'string')

// File extension and fence alias resolution.
check('extension is case-insensitive (.CSS)', highlightCode('a{b:c}', 'STYLES.CSS').includes('hljs-selector-tag'))
check('extension .mjs -> javascript', highlightCode('const x = 1', 'app.mjs').includes('hljs-keyword'))
check('extension .svg -> xml', highlightCode('<a/>', 'pic.svg').includes('hljs-name'))
check('fence alias is case-insensitive (TypeScript)', highlightFence('TypeScript', 'const x: number = 1').includes('hljs-keyword'))
check('fence alias scss -> css does not crash', typeof highlightFence('scss', '$x:1;.a{color:$x}') === 'string')

// Every token class the bundled grammars can emit must have a CSS rule, or it
// would render unstyled. Samples intentionally exercise at-rules, pseudo
// selectors, attribute selectors, custom properties, and jsdoc/labels.
const broadCss = highlightCode('@media (min-width:10px){.a::before,#id,.cls[attr]:hover{color:var(--brand)!important}}', 'x.css')
const broadJs = highlightCode('/** @param */\nlabel: for (const k of [1]) { if (k) continue label; }', 'x.js')
const stylesheet = fs.readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8')
const styled = new Set([...stylesheet.matchAll(/\.(hljs-[a-z0-9_-]+)/g)].map((m) => m[1]))
const emitted = new Set()
for (const out of [html, inlineHtml, cssOut, js, richJs, json, broadCss, broadJs]) {
  for (const m of out.matchAll(/class="([^"]+)"/g)) {
    for (const cls of m[1].split(/\s+/)) if (cls.startsWith('hljs-')) emitted.add(cls)
  }
}
const unstyled = [...emitted].filter((c) => !styled.has(c))
check(`all emitted hljs classes are styled (${emitted.size} classes)`, unstyled.length === 0)
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
