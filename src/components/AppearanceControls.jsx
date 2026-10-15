import { SunIcon, MoonIcon, MonitorIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'

const THEME_CHOICES = [
  { value: 'light', icon: SunIcon, label: 'theme.lightShort' },
  { value: 'dark', icon: MoonIcon, label: 'theme.darkShort' },
  { value: 'system', icon: MonitorIcon, label: 'theme.system' },
]

// Light / Dark / System picker, shared by the top-bar menu and Settings.
export function ThemeChoice({ value, onChange }) {
  const { t } = useLanguage()
  return (
    <div className="theme-choice" role="radiogroup" aria-label={t('theme.group')}>
      {THEME_CHOICES.map(({ value: v, icon: Icon, label }) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          className={value === v ? 'active' : ''}
          onClick={() => onChange(v)}
        >
          <Icon aria-hidden="true" />
          <span>{t(label)}</span>
        </button>
      ))}
    </div>
  )
}

export function VoiceSwitch({ enabled, onChange }) {
  const { t } = useLanguage()
  return (
    <label className="memory-switch">
      <input
        type="checkbox"
        role="switch"
        checked={enabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="memory-switch-track" aria-hidden="true" />
      <span>{t('voice.label')}</span>
    </label>
  )
}
