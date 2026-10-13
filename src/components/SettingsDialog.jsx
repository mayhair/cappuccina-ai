import { useEffect, useRef, useState } from 'react'
import { PersonIcon, MailIcon, CalendarIcon, ShieldIcon, GlobeIcon, ChevronIcon, ArrowLeftIcon } from './Icons'
import { LanguagePicker } from './LanguagePicker'
import { useLanguage } from '../i18n/LanguageProvider'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { updateProfile, setAdultMode } from '../api/authClient'
import { isAdultBirthday } from '../lib/adultMode'
import { connectGmailUrl, disconnectGmail, gmailStatus } from '../api/gmailClient'
import {
  connectCalendarUrl,
  calendarStatus,
  disconnectCalendar,
} from '../api/calendarClient'

function ProfileForm({ user, onUpdated }) {
  const { t } = useLanguage()
  const [name, setName] = useState(user.displayName)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const dirty = name !== user.displayName

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      const updated = await updateProfile({ name })
      onUpdated(updated)
      setSaved(true)
    } catch (err) {
      setError(err.message || t('settings.error.generic'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-name">
          {t('settings.profile.displayName')}
        </label>
        <input
          id="settings-name"
          className="settings-input"
          type="text"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setSaved(false)
          }}
          autoComplete="name"
          placeholder={t('settings.profile.placeholder')}
        />
      </div>
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="settings-msg ok">{t('settings.profile.updated')}</p>}
      <div className="settings-actions">
        <button type="submit" className="settings-save" disabled={busy || !dirty}>
          {busy ? t('settings.profile.saving') : t('settings.profile.save')}
        </button>
      </div>
    </form>
  )
}

function AdultModeForm({ user, onUpdated }) {
  const { t } = useLanguage()
  const [birthday, setBirthday] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const adult = Boolean(user.adultMode)

  const save = async (enabled) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      onUpdated(await setAdultMode(enabled))
    } catch (err) {
      setError(err.message || t('settings.error.generic'))
    } finally {
      setBusy(false)
    }
  }

  const submit = (e) => {
    e.preventDefault()
    if (!isAdultBirthday(birthday)) {
      setError(t('settings.adult.tooYoung'))
      return
    }
    setBirthday('') // the date is never kept
    save(true)
  }

  if (adult) {
    return (
      <div className="settings-form">
        <p className="settings-status">
          <ShieldIcon />
          {t('settings.adult.on')}
        </p>
        <p className="settings-hint">{t('settings.adult.onHint')}</p>
        {error && (
          <p className="settings-msg error" role="alert">
            {error}
          </p>
        )}
        <div className="settings-actions">
          <button type="button" className="settings-save" onClick={() => save(false)} disabled={busy}>
            {busy ? t('settings.adult.turningOff') : t('settings.adult.turnOff')}
          </button>
        </div>
      </div>
    )
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      <p className="settings-hint">{t('settings.adult.offHint')}</p>
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-birthday">
          {t('settings.adult.birthday')}
        </label>
        <input
          id="settings-birthday"
          className="settings-input"
          type="date"
          value={birthday}
          onChange={(e) => {
            setBirthday(e.target.value)
            setError('')
          }}
        />
      </div>
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}
      <div className="settings-actions">
        <button type="submit" className="settings-save" disabled={busy || !birthday}>
          {busy ? t('settings.adult.enabling') : t('settings.adult.enable')}
        </button>
      </div>
    </form>
  )
}

function ConnectionForm({
  icon: Icon,
  serviceName,
  description,
  connectUrl,
  loadStatus,
  disconnect,
  connectedKey,
  user,
  notice,
  onNoticeClear,
  onUserUpdated,
}) {
  const { t } = useLanguage()
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let canceled = false
    loadStatus()
      .then((s) => {
        if (!canceled) setStatus(s)
      })
      .catch(() => {
        if (!canceled) setStatus({ connected: false })
      })
    return () => {
      canceled = true
    }
  }, [loadStatus])

  const handleDisconnect = async () => {
    setBusy(true)
    setError('')
    try {
      await disconnect()
      setStatus({ connected: false })
      onUserUpdated({ ...user, [connectedKey]: false })
      onNoticeClear()
    } catch (err) {
      setError(err.message || t('settings.connections.disconnectFailed'))
    } finally {
      setBusy(false)
    }
  }

  const connected = status?.connected ?? user[connectedKey]

  return (
    <div className="settings-form">
      <p className="settings-hint">{description}</p>

      {notice === 'connected' && (
        <p className="settings-msg ok">{t('settings.connections.connectedOk', { service: serviceName })}</p>
      )}
      {notice === 'error' && (
        <p className="settings-msg error" role="alert">
          {t('settings.connections.connectFailed', { service: serviceName })}
        </p>
      )}
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}

      <p className="settings-status">
        <Icon />
        {connected
          ? t('settings.connections.isConnected', { service: serviceName })
          : t('settings.connections.isNotConnected', { service: serviceName })}
      </p>

      <div className="settings-actions">
        {connected ? (
          <button
            type="button"
            className="settings-save"
            onClick={handleDisconnect}
            disabled={busy || status === null}
          >
            {busy ? t('settings.connections.disconnecting') : t('settings.connections.disconnect', { service: serviceName })}
          </button>
        ) : (
          <a className="settings-save" href={connectUrl}>
            {t('settings.connections.connect', { service: serviceName })}
          </a>
        )}
      </div>
    </div>
  )
}

function GmailForm({ user, notice, onNoticeClear, onUserUpdated }) {
  const { t } = useLanguage()
  return (
    <ConnectionForm
      icon={MailIcon}
      serviceName={t('settings.connections.gmail')}
      description={t('settings.connections.gmailDesc')}
      connectUrl={connectGmailUrl}
      loadStatus={gmailStatus}
      disconnect={disconnectGmail}
      connectedKey="gmailConnected"
      user={user}
      notice={notice}
      onNoticeClear={onNoticeClear}
      onUserUpdated={onUserUpdated}
    />
  )
}

function CalendarForm({ user, notice, onNoticeClear, onUserUpdated }) {
  const { t } = useLanguage()
  return (
    <ConnectionForm
      icon={CalendarIcon}
      serviceName={t('settings.connections.calendar')}
      description={t('settings.connections.calendarDesc')}
      connectUrl={connectCalendarUrl}
      loadStatus={calendarStatus}
      disconnect={disconnectCalendar}
      connectedKey="calendarConnected"
      user={user}
      notice={notice}
      onNoticeClear={onNoticeClear}
      onUserUpdated={onUserUpdated}
    />
  )
}

export function SettingsDialog({
  user,
  open,
  onClose,
  onUserUpdated,
  gmailNotice = '',
  onGmailNoticeClear = () => {},
  calendarNotice = '',
  onCalendarNoticeClear = () => {},
}) {
  const { lang, setLang, t } = useLanguage()
  const [section, setSection] = useState('account')
  // Mobile shows settings as a two-screen stack: an index that lists every
  // section, and a detail screen per section. Desktop ignores `view` and keeps
  // the nav-rail + pane layout.
  const [view, setView] = useState('index')
  const isMobile = useMediaQuery('(max-width: 768px)')
  const backRef = useRef(null)
  const rowRefs = useRef({})
  const adultEnabled = user.adultModeEnabled !== false

  const sections = [
    {
      key: 'account',
      icon: <PersonIcon />,
      label: t('settings.nav.account'),
      content: (
        <>
          <h3 className="settings-heading">{t('settings.nav.account')}</h3>
          <section className="settings-section">
            <h4>{t('settings.account.profile')}</h4>
            <ProfileForm user={user} onUpdated={onUserUpdated} />
          </section>
          <section className="settings-section">
            <h4>{t('settings.account.signin')}</h4>
            <p className="settings-hint">
              {t('settings.account.signinHint', { email: user.email })}
            </p>
          </section>
        </>
      ),
    },
    {
      key: 'connections',
      icon: <MailIcon />,
      label: t('settings.nav.connections'),
      content: (
        <>
          <h3 className="settings-heading">{t('settings.nav.connections')}</h3>
          <section className="settings-section">
            <h4>{t('settings.connections.gmail')}</h4>
            <GmailForm
              user={user}
              notice={gmailNotice}
              onNoticeClear={onGmailNoticeClear}
              onUserUpdated={onUserUpdated}
            />
          </section>
          <section className="settings-section">
            <h4>{t('settings.connections.calendar')}</h4>
            <CalendarForm
              user={user}
              notice={calendarNotice}
              onNoticeClear={onCalendarNoticeClear}
              onUserUpdated={onUserUpdated}
            />
          </section>
        </>
      ),
    },
    ...(adultEnabled
      ? [
          {
            key: 'adult',
            icon: <ShieldIcon />,
            label: t('settings.nav.adult'),
            content: (
              <>
                <h3 className="settings-heading">{t('settings.nav.adult')}</h3>
                <section className="settings-section">
                  <h4>{t('settings.adult.section')}</h4>
                  <AdultModeForm user={user} onUpdated={onUserUpdated} />
                </section>
              </>
            ),
          },
        ]
      : []),
    {
      key: 'language',
      icon: <GlobeIcon />,
      label: t('settings.nav.language'),
      content: (
        <>
          <h3 className="settings-heading">{t('language.settings.heading')}</h3>
          <section className="settings-section">
            <p className="settings-hint">{t('language.settings.hint')}</p>
            <LanguagePicker value={lang} onChange={setLang} />
          </section>
        </>
      ),
    },
  ]
  // Turning adult mode off from inside its own section removes that section, so
  // the pane falls back to "nothing" and the mobile stack back to the index.
  const active = sections.find((s) => s.key === section)
  const showDetail = isMobile && view === 'detail' && Boolean(active)

  // Reset to the first section only when the dialog opens. `onClose` is a new
  // function on every parent render, so keying this off it would also fire on
  // an unrelated user update (e.g. saving the adult-mode flag) and yank the
  // pane back to Account.
  useEffect(() => {
    if (!open) return
    setSection('account')
    setView('index')
  }, [open])

  useEffect(() => {
    if (!open) return
    const esc = (e) => {
      if (e.key !== 'Escape') return
      // On mobile Escape steps back out of the detail screen before closing.
      if (isMobile && view === 'detail') setView('index')
      else onClose()
    }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [open, onClose, isMobile, view])

  // Move focus with the stack: the back button when a section opens, the row
  // that was visited when it closes. The first render is skipped so opening
  // the dialog doesn't move focus.
  const openedRef = useRef(false)
  useEffect(() => {
    if (!open) {
      openedRef.current = false
      return
    }
    if (!openedRef.current) {
      openedRef.current = true
      return
    }
    if (!isMobile) return
    if (view === 'detail') backRef.current?.focus()
    else rowRefs.current[section]?.focus()
  }, [open, isMobile, view, section])

  if (!open) return null

  const closeButton = (
    <button
      type="button"
      className="settings-close"
      aria-label={t('settings.close')}
      onClick={onClose}
    >
      ✕
    </button>
  )

  return (
    <div
      className="settings-veil"
      role="dialog"
      aria-modal="true"
      aria-label={t('settings.title')}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="settings">
        {isMobile ? (
          <>
            <div className="settings-topbar">
              {showDetail ? (
                <button
                  ref={backRef}
                  type="button"
                  className="settings-back"
                  aria-label={t('settings.back')}
                  onClick={() => setView('index')}
                >
                  <ArrowLeftIcon />
                  {t('settings.title')}
                </button>
              ) : (
                <h2 className="settings-mobile-title">{t('settings.title')}</h2>
              )}
              {closeButton}
            </div>
            {showDetail ? (
              <div className="settings-pane">{active.content}</div>
            ) : (
              <ul className="settings-index">
                {sections.map(({ key, icon, label }) => (
                  <li key={key}>
                    <button
                      ref={(el) => {
                        rowRefs.current[key] = el
                      }}
                      type="button"
                      className="settings-index-item"
                      onClick={() => {
                        setSection(key)
                        setView('detail')
                      }}
                    >
                      {icon}
                      <span className="settings-index-label">{label}</span>
                      <ChevronIcon className="settings-chevron" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            <aside className="settings-nav">
              {closeButton}
              {sections.map(({ key, icon, label }) => (
                <button
                  key={key}
                  type="button"
                  className={`settings-nav-item${section === key ? ' active' : ''}`}
                  onClick={() => setSection(key)}
                >
                  {icon}
                  {label}
                </button>
              ))}
            </aside>
            <div className="settings-pane">{active && active.content}</div>
          </>
        )}
      </div>
    </div>
  )
}
