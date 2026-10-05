// Syntax highlighting for the webpage canvas Code pane, chat tool-args, and
// fenced markdown blocks. Prism with only the core plus the handful of grammars
// this app renders, so the bundle stays small. Each entry point returns an HTML
// string of escaped text and `token` spans; callers render it with
// dangerouslySetInnerHTML. Prism escapes the source, so this never injects
// page-authored markup.
import './prismSetup.js'
import Prism from 'prismjs/components/prism-core.js'
// Import order is load-bearing: css and javascript hook into the markup grammar,
// and javascript extends clike.
import 'prismjs/components/prism-markup.js' // also aliases html/xml/svg/mathml
import 'prismjs/components/prism-css.js'
import 'prismjs/components/prism-clike.js'
import 'prismjs/components/prism-javascript.js'
import 'prismjs/components/prism-json.js'

// Webpage files: map by extension. Anything unknown is highlighted as JS, which
// matches the canvas's previous default.
const LANGUAGE_BY_EXT = {
  html: 'markup',
  htm: 'markup',
  xhtml: 'markup',
  svg: 'markup',
  css: 'css',
  json: 'json',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'javascript',
  ts: 'javascript',
}

// Markdown fences: accept common aliases, fall back to plainly escaped text for
// languages we do not bundle.
const LANGUAGE_BY_FENCE = {
  html: 'markup',
  htm: 'markup',
  xml: 'markup',
  svg: 'markup',
  css: 'css',
  scss: 'css',
  less: 'css',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'javascript',
  ts: 'javascript',
  typescript: 'javascript',
  tsx: 'javascript',
  json: 'json',
}

function extension(fileName) {
  const name = String(fileName ?? '')
  const dot = name.lastIndexOf('.')
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase()
}

// Prism throws ("The language ... has no grammar") when the grammar is missing,
// so an unknown language falls back to the core's empty plaintext grammar, which
// returns escaped text with no tokens. Prism never throws on malformed source.
function highlight(code, language) {
  const source = code ?? ''
  const grammar = language ? Prism.languages[language] : null
  return Prism.highlight(source, grammar ?? Prism.languages.plaintext, language ?? 'plaintext')
}

export function highlightCode(code, fileName) {
  return highlight(code, LANGUAGE_BY_EXT[extension(fileName)] ?? 'javascript')
}

export function highlightJson(text) {
  return highlight(text, 'json')
}

export function highlightFence(language, code) {
  return highlight(code, LANGUAGE_BY_FENCE[String(language ?? '').toLowerCase()])
}
