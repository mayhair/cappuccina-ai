import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'

// Cloudflare Turnstile, rendered explicitly so React owns the container.
// The script loads on first use only, so the landing page stays light for
// everyone who signs in with Google.
// https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/

const SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

let scriptPromise = null

function loadTurnstile() {
  if (window.turnstile) return Promise.resolve(window.turnstile)
  if (!scriptPromise) {
    scriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script')
      script.src = SCRIPT_URL
      script.async = true
      script.onload = () => resolve(window.turnstile)
      script.onerror = () => {
        scriptPromise = null
        reject(new Error('Turnstile failed to load'))
      }
      document.head.appendChild(script)
    })
  }
  return scriptPromise
}

// Turnstile has no Latin-script Urdu; Urdu users get it in Urdu.
const TURNSTILE_LANG = { 'ur-Latn': 'ur' }

/**
 * Calls onToken(token) when the check passes and onToken('') when the token
 * expires or fails. The ref's reset() asks for a fresh token: each one works
 * for a single sign-up attempt.
 */
export const Turnstile = forwardRef(function Turnstile({ siteKey, theme, lang, onToken }, ref) {
  const box = useRef(null)
  const widget = useRef(null)
  const onTokenRef = useRef(onToken)
  onTokenRef.current = onToken

  useImperativeHandle(ref, () => ({
    reset() {
      onTokenRef.current('')
      if (widget.current != null) window.turnstile?.reset(widget.current)
    },
  }))

  useEffect(() => {
    let canceled = false
    loadTurnstile()
      .then((turnstile) => {
        if (canceled || !box.current) return
        widget.current = turnstile.render(box.current, {
          sitekey: siteKey,
          theme: theme === 'dark' ? 'dark' : 'light',
          language: TURNSTILE_LANG[lang] ?? lang,
          size: 'flexible',
          callback: (token) => onTokenRef.current(token),
          'expired-callback': () => onTokenRef.current(''),
          'error-callback': () => onTokenRef.current(''),
        })
      })
      .catch(() => {
        if (!canceled) onTokenRef.current('')
      })
    return () => {
      canceled = true
      if (widget.current != null) window.turnstile?.remove(widget.current)
      widget.current = null
    }
  }, [siteKey, theme, lang])

  return <div className="turnstile-box" ref={box} />
})
