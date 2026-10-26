import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import en from './en'
import ur from './ur'
import urLatn from './ur-Latn'

const STORAGE_KEY = 'cappuccina-lang'

// Adding a language = one entry here + one dictionary file.
export const LANGUAGES = [
  { code: 'en', label: 'English', dir: 'ltr', dict: en },
  { code: 'ur', label: 'اردو', dir: 'rtl', dict: ur },
  { code: 'ur-Latn', label: 'Urdu (Latin)', dir: 'ltr', dict: urLatn },
]

const FALLBACK_LANG = 'en'
const byCode = Object.fromEntries(LANGUAGES.map((l) => [l.code, l]))

function readStoredLang() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    return saved && byCode[saved] ? saved : null
  } catch {
    return null
  }
}

/**
 * Best supported language for the browser's preferences: an exact tag match
 * (ur-Latn), else the first supported language with the same base (ur-PK -> ur).
 */
function detectLanguage() {
  const prefs = (typeof navigator !== 'undefined' && navigator.languages?.length
    ? navigator.languages
    : [navigator?.language]
  ).filter(Boolean)
  for (const tag of prefs) {
    const wanted = tag.toLowerCase()
    const exact = LANGUAGES.find((l) => l.code.toLowerCase() === wanted)
    if (exact) return exact.code
    const base = wanted.split('-')[0]
    const sameBase = LANGUAGES.find((l) => l.code.toLowerCase().split('-')[0] === base)
    if (sameBase) return sameBase.code
  }
  return FALLBACK_LANG
}

/** Look up `key` in `lang`, falling back to English, then to the key itself. */
export function translate(lang, key, vars) {
  const raw = byCode[lang]?.dict[key] ?? en[key] ?? key
  if (!vars) return raw
  return raw.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m))
}

const RICH_TAG = /<(i|b|em|strong)>(.*?)<\/\1>/g

/** Like translate(), but turns <i>/<b>/<em>/<strong> in the string into elements. */
export function translateRich(lang, key, vars) {
  const text = translate(lang, key, vars)
  const parts = []
  let last = 0
  let n = 0
  for (const m of text.matchAll(RICH_TAG)) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const Tag = m[1]
    parts.push(<Tag key={n++}>{m[2]}</Tag>)
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

const LanguageContext = createContext(null)

// `titleKey` names the tab title, so a page other than the app can use its own.
export function LanguageProvider({ children, titleKey = 'app.title' }) {
  const [stored, setStored] = useState(readStoredLang)
  // True while a language dialog is on screen, so other first-run dialogs wait.
  const [pickerOpen, setPickerOpen] = useState(false)
  // Until the user chooses, follow the browser's language (the picker pre-selects it).
  const [detected] = useState(detectLanguage)
  const lang = stored ?? detected
  const dir = byCode[lang].dir

  useEffect(() => {
    document.documentElement.lang = lang
    document.documentElement.dir = dir
    document.title = translate(lang, titleKey)
  }, [lang, dir, titleKey])

  const setLang = useCallback((code) => {
    if (!byCode[code]) return
    try {
      localStorage.setItem(STORAGE_KEY, code)
    } catch {
      // Storage unavailable: the choice still applies for this session.
    }
    setStored(code)
  }, [])

  const t = useCallback((key, vars) => translate(lang, key, vars), [lang])

  const tr = useCallback((key, vars) => translateRich(lang, key, vars), [lang])

  const value = useMemo(
    () => ({ lang, dir, setLang, t, tr, hasChosen: stored !== null, pickerOpen, setPickerOpen }),
    [lang, dir, setLang, t, tr, stored, pickerOpen],
  )

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
}

export function useLanguage() {
  const ctx = useContext(LanguageContext)
  if (!ctx) throw new Error('useLanguage must be used inside <LanguageProvider>')
  return ctx
}
