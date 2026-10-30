import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import {
  SunIcon,
  PersonIcon,
  MailIcon,
  CalendarIcon,
  ShieldIcon,
  GlobeIcon,
  BrainIcon,
  BookIcon,
  ChevronIcon,
  ArrowLeftIcon,
  CheckIcon,
  DownloadIcon,
  TrashIcon,
  DocumentIcon,
} from './Icons'
import { EmojiText } from '../lib/emoji'
import { LanguagePicker } from './LanguagePicker'
import { ThemeChoice, VoiceSwitch } from './AppearanceControls'
import { MemoryManager } from './MemoryManager'
import { StorySettings } from './StorySettings'
import { CharacterManager } from './CharacterManager'
import { useLanguage } from '../i18n/LanguageProvider'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { updateProfile, setAdultMode, deleteAccount, changePassword } from '../api/authClient'
import { authErrorText } from './EmailAuthForm'
import { isAdultBirthday } from '../lib/adultMode'
import {
  subscribe as subscribeInstall,
  getSnapshot as getInstallSnapshot,
  promptInstall,
  platformHint,
} from '../lib/pwa'
import { SHOW_CONNECTIONS_TESTING_NOTE } from '../config'
import { saveReturnTo } from '../lib/router'
import { connectGmailUrl, disconnectGmail, gmailStatus } from '../api/gmailClient'
import {
  connectCalendarUrl,
  calendarStatus,
  disconnectCalendar,
} from '../api/calendarClient'

function ProfileForm({ user, onUpdated }) {
  const { t } = useLanguage()
  const [name, setName] = useState(user.displayName)
  const [showEmail, setShowEmail] = useState(false)
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
        <span className="settings-label">{t('auth.email.email')}</span>
        <span className="settings-email">
          {showEmail && <span className="settings-email-value">{user.email}</span>}
          <button
            type="button"
            className="settings-view"
            aria-expanded={showEmail}
            onClick={() => setShowEmail((shown) => !shown)}
          >
            {t(showEmail ? 'settings.profile.hideEmail' : 'settings.profile.showEmail')}
          </button>
        </span>
      </div>
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

// Change the password, or add a first one to an account that signs in with
// Google only. Other devices get signed out; this one stays signed in.
function PasswordForm({ user, onUpdated }) {
  const { t, lang } = useLanguage()
  const hasPassword = user.hasPassword === true
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      const updated = await changePassword({ currentPassword: current, newPassword: next, lang })
      onUpdated(updated)
      setCurrent('')
      setNext('')
      setSaved(true)
    } catch (err) {
      setError(authErrorText(t, err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      {/* Lets password managers pair the new password with the account. */}
      <input type="email" value={user.email} autoComplete="username" readOnly hidden />
      {hasPassword && (
        <div className="settings-row">
          <label className="settings-label" htmlFor="settings-current-password">
            {t('settings.password.current')}
          </label>
          <input
            id="settings-current-password"
            className="settings-input"
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
            maxLength={256}
            required
          />
        </div>
      )}
      <div className="settings-row">
        <label className="settings-label" htmlFor="settings-new-password">
          {t('settings.password.new')}
        </label>
        <input
          id="settings-new-password"
          className="settings-input"
          type="password"
          value={next}
          onChange={(e) => {
            setNext(e.target.value)
            setSaved(false)
          }}
          autoComplete="new-password"
          minLength={8}
          maxLength={256}
          required
        />
      </div>
      {!hasPassword && <p className="settings-hint">{t('settings.password.addHint')}</p>}
      {error && (
        <p className="settings-msg error" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="settings-msg ok">{t('settings.password.saved')}</p>}
      <div className="settings-actions">
        <button type="submit" className="settings-save" disabled={busy || !next}>
          {busy
            ? t('settings.profile.saving')
            : t(hasPassword ? 'settings.password.change' : 'settings.password.add')}
        </button>
      </div>
    </form>
  )
}

function signinHintKey(user) {
  if (user.googleLinked && user.hasPassword) return 'settings.account.signinHintBoth'
  if (user.hasPassword) return 'settings.account.signinHintPassword'
  return 'settings.account.signinHint'
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

// The last stop before the account is gone. The red button stays disabled until
// the user types their own email, so it can't be confirmed by reflex.
function DeleteAccountModal({ email, onCancel, onDeleted }) {
  const { t, tr } = useLanguage()
  const [typed, setTyped] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const matches = typed.trim().toLowerCase() === email.trim().toLowerCase()

  // Capture phase, so Escape closes only this pop-up and never reaches the
  // Settings dialog's own (bubbling) Escape handler underneath.
  useEffect(() => {
    const esc = (e) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      if (!busy) onCancel()
    }
    window.addEventListener('keydown', esc, true)
    return () => window.removeEventListener('keydown', esc, true)
  }, [busy, onCancel])

  const submit = async (e) => {
    e.preventDefault()
    if (busy || !matches) return
    setBusy(true)
    setError('')
    try {
      await deleteAccount()
      onDeleted()
    } catch (err) {
      setError(err.message || t('settings.error.generic'))
      setBusy(false)
    }
  }

  // Portaled to <body>: .settings has a backdrop-filter, which would otherwise
  // pin this fixed-position veil inside the settings box.
  return createPortal(
    <div
      className="modal-veil danger-veil"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="delete-account-title"
      aria-describedby="delete-account-body"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}
    >
      <form className="modal modal-danger" onSubmit={submit}>
        <button
          type="button"
          className="modal-x"
          aria-label={t('common.close')}
          onClick={onCancel}
          disabled={busy}
        >
          ✕
        </button>
        <div className="modal-avatar">
          <img src="/plates/msg-avatar.webp" alt="" />
        </div>
        <h2 id="delete-account-title">
          <EmojiText text={t('settings.account.deleteModal.title')} />
        </h2>
        <p id="delete-account-body">{tr('settings.account.deleteModal.body')}</p>
        <ul className="modal-danger-list">
          <li>{t('settings.account.deleteModal.listChats')}</li>
          <li>{t('settings.account.deleteModal.listFiles')}</li>
          <li>{t('settings.account.deleteModal.listPages')}</li>
          <li>{t('settings.account.deleteModal.listMemories')}</li>
          <li>{t('settings.account.deleteModal.listConnections')}</li>
        </ul>
        <p className="modal-danger-warning">{tr('settings.account.deleteModal.warning')}</p>
        <label className="modal-danger-label" htmlFor="delete-account-email">
          {tr('settings.account.deleteModal.confirmLabel', { email })}
        </label>
        <input
          id="delete-account-email"
          className="modal-danger-input"
          type="text"
          inputMode="email"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={email}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          autoFocus
          disabled={busy}
        />
        {error && (
          <p className="settings-msg error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="btn-keep" onClick={onCancel} disabled={busy}>
            {t('settings.account.deleteNo')}
          </button>
          <button type="submit" className="btn-toss" disabled={busy || !matches}>
            {busy ? t('settings.account.deleting') : t('settings.account.deleteModal.confirm')}
          </button>
        </div>
        {/* Removing webpage containers can first have to start the container
            engine, so the request may take a while. */}
        {busy && (
          <p className="settings-hint settings-wait" role="status">
            {t('settings.account.deleteWait')}
          </p>
        )}
      </form>
    </div>,
    document.body,
  )
}

// Folded away by default so the delete button is never one stray click from
// the profile form.
function DeleteAccountForm({ email, onDeleted }) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const deleteRef = useRef(null)

  const cancel = useCallback(() => {
    setConfirming(false)
    deleteRef.current?.focus()
  }, [])

  return (
    <div className={`danger-zone${open ? ' open' : ''}`}>
      <button
        type="button"
        className="danger-zone-head"
        aria-expanded={open}
        aria-controls="danger-zone-body"
        onClick={() => setOpen((o) => !o)}
      >
        <ChevronIcon className={`chev${open ? ' open' : ''}`} />
        {t('settings.account.deleteSection')}
      </button>
      {open && (
        <div id="danger-zone-body" className="danger-zone-body">
          <p className="settings-hint">{t('settings.account.deleteHint')}</p>
          <div className="settings-actions">
            <button
              ref={deleteRef}
              type="button"
              className="danger-zone-delete"
              onClick={() => setConfirming(true)}
            >
              <TrashIcon />
              {t('settings.account.delete')}
            </button>
          </div>
        </div>
      )}
      {confirming && <DeleteAccountModal email={email} onCancel={cancel} onDeleted={onDeleted} />}
    </div>
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

  // The OAuth round trip ends on "/"; remember the chat to return to.
  const rememberPlace = () => saveReturnTo(window.location.pathname)

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
          <a className="settings-save" href={connectUrl} onClick={rememberPlace}>
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

function TestingNote() {
  const { t } = useLanguage()
  return (
    <p className="settings-testing-note" role="note">
      {t('settings.connections.testingNote')}
    </p>
  )
}

function InstallForm() {
  const { t } = useLanguage()
  const { installed, canPrompt } = useSyncExternalStore(subscribeInstall, getInstallSnapshot)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState('')
  const platform = platformHint()

  const install = async () => {
    if (busy) return
    setBusy(true)
    setOutcome('')
    try {
      setOutcome(await promptInstall())
    } finally {
      setBusy(false)
    }
  }

  if (installed) {
    return (
      <div className="settings-form">
        <p className="settings-status">
          <CheckIcon />
          {t('settings.install.installed')}
        </p>
      </div>
    )
  }

  if (outcome === 'accepted') {
    return (
      <div className="settings-form">
        <p className="settings-msg ok" role="status">
          {t('settings.install.accepted')}
        </p>
      </div>
    )
  }

  let guidance
  if (canPrompt) {
    guidance = (
      <>
        <p className="settings-hint">{t('settings.install.hint')}</p>
        <div className="settings-actions">
          <button type="button" className="settings-save" onClick={install} disabled={busy}>
            {busy ? t('settings.install.installing') : t('settings.install.button')}
          </button>
        </div>
      </>
    )
  } else if (platform === 'ios') {
    guidance = (
      <>
        <p className="settings-hint">{t('settings.install.iosIntro')}</p>
        <ol className="settings-steps">
          <li>{t('settings.install.iosStep1')}</li>
          <li>{t('settings.install.iosStep2')}</li>
          <li>{t('settings.install.iosStep3')}</li>
        </ol>
      </>
    )
  } else if (platform === 'safari-desktop') {
    guidance = <p className="settings-hint">{t('settings.install.safari')}</p>
  } else if (platform === 'firefox') {
    guidance = <p className="settings-hint">{t('settings.install.firefox')}</p>
  } else {
    guidance = <p className="settings-hint">{t('settings.install.generic')}</p>
  }

  return (
    <div className="settings-form">
      {guidance}
      {outcome === 'dismissed' && (
        <p className="settings-msg neutral" role="status">
          {t('settings.install.dismissed')}
        </p>
      )}
    </div>
  )
}

export function SettingsDialog({
  user,
  open,
  onClose,
  onUserUpdated,
  // Called once the account is gone (the server has already cleared the cookie).
  onAccountDeleted = () => {},
  // Section the URL asks for (e.g. 'memory' from a chat card); empty means the
  // first one (the index on mobile). Follows the URL while the dialog is open.
  initialSection = '',
  // Told when the user picks a section ('' = back to the mobile index), so the
  // URL can follow.
  onSectionChange = () => {},
  gmailNotice = '',
  onGmailNoticeClear = () => {},
  calendarNotice = '',
  onCalendarNoticeClear = () => {},
  theme = 'system',
  onTheme = () => {},
  voice = false,
  onVoice = null,
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
  const gmailEnabled = user.gmailEnabled === true
  const calendarEnabled = user.calendarEnabled === true
  const storyEnabled = user.storyModeEnabled !== false

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
              {t(signinHintKey(user))}
            </p>
          </section>
          {user.passwordAuthEnabled !== false && (
            <section className="settings-section">
              <h4>{t('settings.password.section')}</h4>
              <PasswordForm user={user} onUpdated={onUserUpdated} />
            </section>
          )}
          <section className="settings-section">
            <DeleteAccountForm email={user.email} onDeleted={onAccountDeleted} />
          </section>
        </>
      ),
    },
    {
      key: 'appearance',
      icon: <SunIcon />,
      label: t('settings.nav.appearance'),
      content: (
        <>
          <h3 className="settings-heading">{t('settings.nav.appearance')}</h3>
          <section className="settings-section">
            <h4>{t('theme.group')}</h4>
            <div className="settings-form">
              <ThemeChoice value={theme} onChange={onTheme} />
              <p className="settings-hint">{t('settings.appearance.themeHint')}</p>
            </div>
          </section>
          {onVoice && (
            <section className="settings-section">
              <h4>{t('settings.appearance.voice')}</h4>
              <div className="settings-form">
                <VoiceSwitch enabled={voice} onChange={onVoice} />
                <p className="settings-hint">{t('settings.appearance.voiceHint')}</p>
              </div>
            </section>
          )}
        </>
      ),
    },
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
    {
      key: 'memory',
      icon: <BrainIcon />,
      label: t('settings.nav.memory'),
      content: (
        <>
          <h3 className="settings-heading">{t('settings.nav.memory')}</h3>
          <MemoryManager user={user} onUserUpdated={onUserUpdated} />
        </>
      ),
    },
    ...(storyEnabled
      ? [
          {
            key: 'story',
            icon: <BookIcon />,
            label: t('settings.nav.story'),
            content: (
              <>
                <h3 className="settings-heading">{t('settings.nav.story')}</h3>
                <StorySettings user={user} onUserUpdated={onUserUpdated} />
                <CharacterManager />
              </>
            ),
          },
        ]
      : []),
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
    ...(gmailEnabled || calendarEnabled
      ? [
          {
            key: 'connections',
            icon: <MailIcon />,
            label: t('settings.nav.connections'),
            content: (
              <>
                <h3 className="settings-heading">{t('settings.nav.connections')}</h3>
                {SHOW_CONNECTIONS_TESTING_NOTE && <TestingNote />}
                {gmailEnabled && (
                  <section className="settings-section">
                    <h4>{t('settings.connections.gmail')}</h4>
                    <GmailForm
                      user={user}
                      notice={gmailNotice}
                      onNoticeClear={onGmailNoticeClear}
                      onUserUpdated={onUserUpdated}
                    />
                  </section>
                )}
                {calendarEnabled && (
                  <section className="settings-section">
                    <h4>{t('settings.connections.calendar')}</h4>
                    <CalendarForm
                      user={user}
                      notice={calendarNotice}
                      onNoticeClear={onCalendarNoticeClear}
                      onUserUpdated={onUserUpdated}
                    />
                  </section>
                )}
              </>
            ),
          },
        ]
      : []),
    {
      key: 'install',
      icon: <DownloadIcon />,
      label: t('settings.nav.install'),
      content: (
        <>
          <h3 className="settings-heading">{t('settings.nav.install')}</h3>
          <section className="settings-section">
            <h4>{t('settings.install.section')}</h4>
            <InstallForm />
          </section>
        </>
      ),
    },
    {
      key: 'about',
      icon: <DocumentIcon />,
      label: t('settings.nav.about'),
      content: (
        <>
          <h3 className="settings-heading">{t('settings.nav.about')}</h3>
          <div className="settings-form">
            {[
              ['terms', t('settings.about.terms')],
              ['privacy', t('settings.about.privacy')],
            ].map(([slug, label]) => (
              <div key={slug} className="settings-row">
                <span className="settings-label">{label}</span>
                <a
                  className="settings-view"
                  href={`/${slug}`}
                  target="_blank"
                  rel="noopener"
                  aria-label={`${t('settings.about.view')}: ${label}`}
                >
                  {t('settings.about.view')}
                </a>
              </div>
            ))}
          </div>
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
  // pane back to Account. A layout effect, so opening straight onto a section
  // never paints the previous one first.
  useLayoutEffect(() => {
    if (!open) return
    setSection(initialSection || 'account')
    setView(initialSection ? 'detail' : 'index')
  }, [open])

  // The URL moved to another section while the dialog stayed open.
  useLayoutEffect(() => {
    if (!open) return
    if (initialSection) {
      setSection(initialSection)
      setView('detail')
    } else {
      setView('index')
    }
  }, [initialSection])

  const showSection = (key) => {
    setSection(key)
    setView('detail')
    onSectionChange(key)
  }

  const showIndex = () => {
    setView('index')
    onSectionChange('')
  }

  useEffect(() => {
    if (!open) return
    const esc = (e) => {
      if (e.key !== 'Escape') return
      // On mobile Escape steps back out of the detail screen before closing.
      if (isMobile && view === 'detail') showIndex()
      else onClose()
    }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [open, onClose, isMobile, view, onSectionChange])

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
                  onClick={showIndex}
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
                      onClick={() => showSection(key)}
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
                  onClick={() => showSection(key)}
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
