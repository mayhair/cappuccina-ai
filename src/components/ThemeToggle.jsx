import { SunIcon, MoonIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'

export function ThemeToggle({ theme, onChange }) {
  const { t } = useLanguage()
  return (
    <div className="theme-toggle" role="group" aria-label={t('theme.group')}>
      <button
        type="button"
        className={`seg${theme === 'light' ? ' active' : ''}`}
        aria-label={t('theme.light')}
        aria-pressed={theme === 'light'}
        onClick={() => onChange('light')}
      >
        <SunIcon />
      </button>
      <button
        type="button"
        className={`seg${theme === 'dark' ? ' active' : ''}`}
        aria-label={t('theme.dark')}
        aria-pressed={theme === 'dark'}
        onClick={() => onChange('dark')}
      >
        <MoonIcon />
      </button>
    </div>
  )
}
