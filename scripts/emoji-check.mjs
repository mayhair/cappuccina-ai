import assert from 'node:assert/strict'
import { createServer } from 'vite'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
try {
  const { EmojiText } = await vite.ssrLoadModule('/src/lib/emoji.jsx')
  const html = renderToStaticMarkup(
    React.createElement(EmojiText, { text: 'Hi 🇦🇹 and ❤️ done' }),
  )
  const imgs = html.match(/class="emoji"/g) ?? []
  assert.equal(imgs.length, 2, `expected 2 emoji images, got ${imgs.length}: ${html}`)
  assert.ok(html.includes('1f1e6-1f1f9.svg'), `missing flag SVG: ${html}`)
  assert.ok(html.includes('2764.svg'), `missing heart SVG: ${html}`)
  assert.ok(html.includes('Hi') && html.includes('done'), `plain text lost: ${html}`)
  console.log('emoji-check: ok')
} finally {
  await vite.close()
}
