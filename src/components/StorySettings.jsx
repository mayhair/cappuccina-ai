import { useState } from 'react'
import { useLanguage } from '../i18n/LanguageProvider'
import { setStoryChoices } from '../api/authClient'

// The Story mode section of Settings: whether Cappuccina offers suggested
// next moves as buttons under her story replies. Saved on the user, so it
// applies to every story chat.
export function StorySettings({ user, onUserUpdated }) {
  const { t } = useLanguage()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const enabled = user.storyChoices !== false

  const toggle = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      onUserUpdated(await setStoryChoices(!enabled))
    } catch (err) {
      setError(err.message || t('settings.memory.togglingFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="settings-section">
      <h4>{t('settings.story.choices')}</h4>
      <div className="settings-form">
        <label className="memory-switch">
          <input type="checkbox" role="switch" checked={enabled} onChange={toggle} disabled={busy} />
          <span className="memory-switch-track" aria-hidden="true" />
          <span>{t('settings.story.choices')}</span>
        </label>
        <p className="settings-hint">{t('settings.story.choicesHint')}</p>
        {error && <p className="settings-msg error" role="alert">{error}</p>}
      </div>
    </section>
  )
}
