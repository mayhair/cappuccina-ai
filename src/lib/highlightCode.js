// Syntax highlighting for the webpage canvas Code pane, chat tool-args, and
// fenced markdown blocks. Uses highlight.js with only the core plus the handful
// of languages this app renders, so the bundle stays small. Each entry point
// returns an HTML string of escaped text and `hljs-*` token spans; callers render
// it with dangerouslySetInnerHTML. highlight.js escapes the source, so this never
// injects page-authored markup.
import hljs from 'highlight.js/lib/core'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import plaintext from 'highlight.js/lib/languages/plaintext'

// xml also covers HTML; keep the list explicit so unrelated grammars stay out.
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('plaintext', plaintext)

// Webpage files: map by extension. Anything unknown is highlighted as JS, which
// matches the canvas's previous default.
const LANGUAGE_BY_EXT = {
  html: 'xml',
  htm: 'xml',
  xhtml: 'xml',
  svg: 'xml',
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
  html: 'xml',
  htm: 'xml',
  xml: 'xml',
  svg: 'xml',
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

// Prefer the caller's language, but never let a missing grammar throw on a
// partially streamed or malformed file (ignoreIllegals).
function highlight(code, language) {
  const source = code ?? ''
  const lang = hljs.getLanguage(language) ? language : 'plaintext'
  return hljs.highlight(source, { language: lang, ignoreIllegals: true }).value
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
