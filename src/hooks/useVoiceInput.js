import { useCallback, useEffect, useRef, useState } from 'react'
import { translate, useLanguage } from '../i18n/LanguageProvider'

// Web Speech API speech-to-text. Free (no API key), but only in Chromium- and
// WebKit-based browsers: Firefox doesn't ship it, and Brave exposes the
// interface yet fails at runtime because it has no access to Google's
// transcription backend. Feature detection plus a runtime fallback hide the
// mic on both. Chrome routes audio to Google's free recognition service, so a
// dictation session needs an internet connection.

const getRecognitionCtor = () =>
  typeof window !== 'undefined'
    ? window.SpeechRecognition || window.webkitSpeechRecognition || null
    : null

export function useVoiceInput({ onTranscript, disabled = false } = {}) {
  // Read through a ref so long-lived callbacks always use the current language.
  const { lang } = useLanguage()
  const langRef = useRef(lang)
  langRef.current = lang
  const tt = (key, vars) => translate(langRef.current, key, vars)
  const [supported, setSupported] = useState(() => Boolean(getRecognitionCtor()))
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState('')

  const recognitionRef = useRef(null)
  const listeningRef = useRef(false)
  const transcriptRef = useRef(onTranscript)
  transcriptRef.current = onTranscript

  // Brave reports the interface as present but every session errors out, so
  // ask it directly (the API is async) before offering the button.
  useEffect(() => {
    if (!getRecognitionCtor()) {
      setSupported(false)
      return
    }
    let cancelled = false
    Promise.resolve(window.navigator.brave?.isBrave?.())
      .then((isBrave) => {
        if (!cancelled && isBrave) setSupported(false)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const stop = useCallback(() => {
    listeningRef.current = false
    setListening(false)
    setInterim('')
    recognitionRef.current?.stop()
  }, [])

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor()
    if (!Ctor || disabled || listeningRef.current) return

    const recognition = new Ctor()
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = navigator.language || 'en-US'

    recognition.onresult = (event) => {
      let finalText = ''
      let interimText = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        if (result.isFinal) finalText += result[0].transcript
        else interimText += result[0].transcript
      }
      if (interimText) setInterim(interimText)
      if (finalText) {
        setInterim('')
        transcriptRef.current?.(finalText)
      }
    }

    recognition.onerror = (event) => {
      const code = event.error
      if (code === 'aborted') return
      if (code === 'no-speech') {
        setInterim('')
        return
      }
      listeningRef.current = false
      setListening(false)
      setInterim('')
      if (code === 'network') {
        // No transcription backend (Brave masquerading as Chromium, offline, or
        // an Edge regression). Give up on voice for the rest of the session.
        setSupported(false)
        setError(tt('voiceError.unsupported'))
      } else if (code === 'not-allowed' || code === 'service-not-allowed') {
        setError(tt('voiceError.blocked'))
      } else if (code === 'audio-capture') {
        setError(tt('voiceError.noMic'))
      } else {
        setError(tt('voiceError.noHear'))
      }
    }

    recognition.onend = () => {
      // Chrome ends the session after a lull in speech; keep dictating until
      // the user toggles off or the hook is torn down.
      if (!listeningRef.current) return
      try {
        recognition.start()
      } catch {
        listeningRef.current = false
        setListening(false)
      }
    }

    recognitionRef.current = recognition
    listeningRef.current = true
    setError('')
    setListening(true)
    try {
      recognition.start()
    } catch {
      listeningRef.current = false
      setListening(false)
    }
  }, [disabled])

  const toggle = useCallback(() => {
    if (listeningRef.current) stop()
    else start()
  }, [start, stop])

  // Never leave the mic open: close it when disabled (e.g. while the assistant
  // is streaming) and tear it down on unmount.
  useEffect(() => {
    if (disabled && listeningRef.current) stop()
  }, [disabled, stop])

  useEffect(
    () => () => {
      listeningRef.current = false
      recognitionRef.current?.abort?.()
    },
    [],
  )

  return { supported, listening, interim, error, toggle, stop }
}
