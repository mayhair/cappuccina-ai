import { Fragment } from 'react'
import { renderEmoji } from './emoji'
import { highlightFence } from './highlightCode'

// Minimal markdown -> React elements. Rendering to elements keeps untrusted
// model output injection-safe; fenced code is escaped and tokenized by
// Prism before it is injected as HTML.
// ponytail: handles the common chat cases (bold, italic, code, code blocks,
// lists, headings, quotes, links). If fuller CommonMark fidelity is ever
// needed, swap this for react-markdown.
// Bare domains (github.com/user/repo, cinejoy.pk) that the model forgot to
// prefix with a scheme. TLDs are allow-listed so file names (readme.md,
// app.jsx, install.sh) are not mistaken for links.
const BARE_DOMAIN_TLDS =
  'com|org|net|io|dev|app|co|me|tv|fm|gg|xyz|info|edu|gov|uk|us|ca|au|in|ai|to|cc|ly|st|nu|ws|sx|pk|gl|pw|ru|is|be|de|fr|it|nl|se|no|es|pl|jp|kr|cn|br|mx|ch|at|ie|nz|sg|hk|link|live|site|online|store|tech|wiki|club|page|space|news|blog|stream|website|art|fun|games'

const INLINE = new RegExp(
  [
    '(`+)([\\s\\S]*?)\\1', // 1,2  code span
    '\\*\\*([^\\s*](?:[\\s\\S]*?[^\\s*])?)\\*\\*', // 3  **bold**
    '~~([^\\s~](?:[\\s\\S]*?[^\\s~])?)~~', // 4  ~~strike~~
    '\\*([^\\s*](?:[^*\\n]*?[^\\s*])?)\\*', // 5  *italic*
    '\\[([^\\]]+)\\]\\(([^)\\s]+)\\)', // 6,7  [text](href)
    '(https?:\\/\\/[^\\s<>()\\[\\]]*[^\\s<>()\\[\\].,!?;:])', // 8  bare url
    '(?<![\\w@./-])((?:www\\.)?(?:[a-z0-9-]+\\.)+(?:' +
      BARE_DOMAIN_TLDS +
      ')(?:\\/[^\\s<>()\\[\\]]*[^\\s<>()\\[\\].,!?;:])?)', // 9  bare domain
  ].join('|'),
  'g',
)

const SAFE_HREF = /^(https?:\/\/|mailto:)/i

const isBlockStart = (line) =>
  /^\s*```/.test(line) ||
  /^#{1,6}\s+/.test(line) ||
  /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line) ||
  /^\s*>\s?/.test(line) ||
  /^\s*[-*+]\s+/.test(line) ||
  /^\s*\d+[.)]\s+/.test(line)

function renderInline(text, prefix) {
  if (!text) return text
  INLINE.lastIndex = 0
  const out = []
  let last = 0
  let n = 0
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(...renderEmoji(text.slice(last, m.index), `${prefix}-${n++}`))
    const key = `${prefix}-${n++}`
    if (m[1] !== undefined) {
      out.push(
        <code key={key} className="md-code">
          {m[2]}
        </code>,
      )
    } else if (m[3] !== undefined) {
      out.push(<strong key={key}>{renderInline(m[3], key)}</strong>)
    } else if (m[4] !== undefined) {
      out.push(<del key={key}>{renderInline(m[4], key)}</del>)
    } else if (m[5] !== undefined) {
      out.push(<em key={key}>{renderInline(m[5], key)}</em>)
    } else if (m[6] !== undefined) {
      out.push(
        SAFE_HREF.test(m[7]) ? (
          <a key={key} href={m[7]} target="_blank" rel="noopener noreferrer">
            {renderInline(m[6], key)}
          </a>
        ) : (
          m[0]
        ),
      )
    } else if (m[8] !== undefined) {
      out.push(
        <a key={key} href={m[8]} target="_blank" rel="noopener noreferrer">
          {m[8]}
        </a>,
      )
    } else if (m[9] !== undefined) {
      out.push(
        <a key={key} href={`https://${m[9]}`} target="_blank" rel="noopener noreferrer">
          {m[9]}
        </a>,
      )
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(...renderEmoji(text.slice(last), `${prefix}-${n++}`))
  return out
}

function inlineWithBreaks(lines, prefix) {
  const out = []
  lines.forEach((line, i) => {
    if (i) out.push(<br key={`${prefix}-br-${i}`} />)
    out.push(<Fragment key={`${prefix}-ln-${i}`}>{renderInline(line, `${prefix}-${i}`)}</Fragment>)
  })
  return out
}

function renderBlocks(text, prefix) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const out = []
  let i = 0
  let n = 0
  const key = () => `${prefix}-${n++}`

  while (i < lines.length) {
    const line = lines[i]

    const fence = line.match(/^\s*```(\w*)\s*$/)
    if (fence) {
      const body = []
      i++
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
        body.push(lines[i])
        i++
      }
      if (i < lines.length) i++
      out.push(
        <pre key={key()} className="md-pre">
          <code
            dangerouslySetInnerHTML={{ __html: highlightFence(fence[1], body.join('\n')) }}
          />
        </pre>,
      )
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/)
    if (heading) {
      const Tag = `h${heading[1].length}`
      const k = key()
      out.push(
        <Tag key={k} className="md-h">
          {renderInline(heading[2], k)}
        </Tag>,
      )
      i++
      continue
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push(<hr key={key()} className="md-hr" />)
      i++
      continue
    }

    if (!line.trim()) {
      i++
      continue
    }

    if (/^\s*>\s?/.test(line)) {
      const body = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ''))
        i++
      }
      const k = key()
      out.push(
        <blockquote key={k} className="md-quote">
          {inlineWithBreaks(body, k)}
        </blockquote>,
      )
      continue
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''))
        i++
      }
      const k = key()
      out.push(
        <ul key={k} className="md-ul">
          {items.map((it, idx) => (
            <li key={idx}>{renderInline(it, `${k}-${idx}`)}</li>
          ))}
        </ul>,
      )
      continue
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        const m = lines[i].match(/^\s*(\d+)[.)]\s+(.*)$/)
        items.push({ num: Number(m[1]), text: m[2] })
        i++
      }
      const k = key()
      out.push(
        <ol key={k} className="md-ol">
          {items.map((it, idx) => (
            <li key={idx} value={it.num}>
              {renderInline(it.text, `${k}-${idx}`)}
            </li>
          ))}
        </ol>,
      )
      continue
    }

    const para = [line]
    i++
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) {
      para.push(lines[i])
      i++
    }
    const k = key()
    out.push(<p key={k}>{inlineWithBreaks(para, k)}</p>)
  }

  return out
}

// The model sometimes cites a generated picture as a link to a made-up
// `image:<id>` target, e.g. "([the new version](image:c7b8...))". The image is
// already attached to the reply, so the dead link is dropped but its text is
// kept and any surrounding parentheses are left alone. Display-only: stored
// text is untouched.
const FAKE_IMAGE_LINK = /\[([^\]\n]*)\]\(image:[^)\s]*\)/gi

export function Markdown({ text }) {
  if (!text) return null
  const shown = text.replace(FAKE_IMAGE_LINK, '$1')
  return <>{renderBlocks(shown, 'md')}</>
}
