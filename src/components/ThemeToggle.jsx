import { SunIcon, MoonIcon } from './Icons'

export function ThemeToggle({ theme, onChange }) {
  return (
    <div className="theme-toggle" role="group" aria-label="Color theme">
      <button
        type="button"
        className={`seg${theme === 'light' ? ' active' : ''}`}
        aria-label="Light mode"
        aria-pressed={theme === 'light'}
        onClick={() => onChange('light')}
      >
        <SunIcon />
      </button>
      <button
        type="button"
        className={`seg${theme === 'dark' ? ' active' : ''}`}
        aria-label="Dark mode"
        aria-pressed={theme === 'dark'}
        onClick={() => onChange('dark')}
      >
        <MoonIcon />
      </button>
    </div>
  )
}
