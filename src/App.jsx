import { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { SidebarControls } from './components/SidebarControls'
import { TopBarTitle } from './components/TopBarTitle'
import { ChatWindow } from './components/ChatWindow'
import { WebpageCanvas } from './components/WebpageCanvas'
import { ThemeToggle } from './components/ThemeToggle'
import { SpeakerToggle } from './components/SpeakerToggle'
import { SparkleIcon, WindowIcon } from './components/Icons'
import { Sky, LandingPage } from './components/LandingPage'
import { AlphaGate } from './components/AlphaGate'
import { LanguageGate } from './components/LanguageGate'
import { SettingsDialog } from './components/SettingsDialog'
import { getMe, logout } from './api/authClient'
import { useChatController } from './hooks/useChatController'
import { useMediaQuery } from './hooks/useMediaQuery'
import tts from './lib/tts'
import { useLanguage } from './i18n/LanguageProvider'

export default function App() {
  const { t } = useLanguage()
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem('cappuccina-theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('cappuccina-theme', theme)
  }, [theme])

  // Voice replies (TTS) are off until the user opts in.
  const [voice, setVoice] = useState(() => localStorage.getItem('cappuccina-voice') === '1')

  useEffect(() => {
    localStorage.setItem('cappuccina-voice', voice ? '1' : '0')
    tts.setEnabled(voice)
  }, [voice])

  const isMobile = useMediaQuery('(max-width: 900px)')

  const [collapsed, setCollapsed] = useState(() => {
    const saved = localStorage.getItem('cappuccina-sidebar-collapsed')
    if (saved === '1') return true
    if (saved === '0') return false
    return window.matchMedia?.('(max-width: 900px)').matches ?? false
  })

  useEffect(() => {
    localStorage.setItem('cappuccina-sidebar-collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  // Track the visual viewport so the composer stays above the phone keyboard.
  // Only the keyboard shrinks vv.height; pinch/browser zoom also shrinks
  // vv.width, so we ignore those (and desktop entirely) to avoid reflowing the
  // app on every zoom frame, which made the layout twist while zooming.
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const root = document.documentElement
    const update = () => {
      const zoomed = vv.width < root.clientWidth - 1
      if (window.innerWidth > 900 || zoomed) {
        root.style.removeProperty('--app-height')
        return
      }
      root.style.setProperty('--app-height', `${Math.round(vv.height)}px`)
    }
    update()
    vv.addEventListener('resize', update)
    window.addEventListener('resize', update)
    return () => {
      vv.removeEventListener('resize', update)
      window.removeEventListener('resize', update)
    }
  }, [])

  const searchInputRef = useRef(null)

  const toggleSidebar = () => setCollapsed((c) => !c)

  const openSidebarAndSearch = () => {
    setCollapsed(false)
    requestAnimationFrame(() => searchInputRef.current?.focus())
  }

  // On mobile the sidebar is a drawer: picking a chat or starting a new one
  // closes it so the chat gets the whole screen.
  const handleSelectChat = (id) => {
    selectChat(id)
    if (isMobile) setCollapsed(true)
  }

  const handleNewChat = () => {
    newChat()
    if (isMobile) setCollapsed(true)
  }

  useEffect(() => {
    if (!isMobile || collapsed) return
    const onKey = (e) => {
      if (e.key === 'Escape') setCollapsed(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isMobile, collapsed])

  const [user, setUser] = useState(null)
  const [authPhase, setAuthPhase] = useState('checking') // checking | landing
  const [authError, setAuthError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Section Settings opens on; empty means the first one. Set to 'memory' by
  // the chat's memory card.
  const [settingsSection, setSettingsSection] = useState('')
  const [gmailNotice, setGmailNotice] = useState('')
  const [calendarNotice, setCalendarNotice] = useState('')

  // OAuth redirects land back on "/" with a short status in the query string.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const error = params.get('auth_error')
    const gmail = params.get('gmail')
    const calendar = params.get('calendar')
    if (error) setAuthError(error)
    if (gmail === 'error' || gmail === 'connected') {
      setGmailNotice(gmail)
      setSettingsOpen(true)
    }
    if (calendar === 'error' || calendar === 'connected') {
      setCalendarNotice(calendar)
      setSettingsOpen(true)
    }
    if (error || gmail || calendar) {
      params.delete('auth_error')
      params.delete('gmail')
      params.delete('calendar')
      const qs = params.toString()
      window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
    }
  }, [])

  useEffect(() => {
    let canceled = false
    getMe()
      .then((me) => {
        if (!canceled) setUser(me)
      })
      .catch(() => {
        if (!canceled) setAuthPhase('landing')
      })
    return () => {
      canceled = true
    }
  }, [])

  const chat = useChatController(Boolean(user))

  const handleLogout = async () => {
    tts.stop()
    await logout()
    setUser(null)
    setAuthPhase('landing')
  }

  if (!user) {
    if (authPhase === 'checking') {
      return (
        <div className="landing">
          <Sky theme={theme} />
        </div>
      )
    }
    return (
      <div className="landing">
        <Sky theme={theme} />
        <LandingPage theme={theme} onTheme={setTheme} error={authError} />
        <LanguageGate />
        <AlphaGate />
      </div>
    )
  }

  const {
    chats,
    activeChat,
    activeChatId,
    draft,
    setDraft,
    query,
    setQuery,
    effort,
    setEffort,
    setMode,
    isStreaming,
    inputRef,
    typingTitleId,
    sendMessage,
    retryMessage,
    editMessage,
    regenerateMessage,
    navigateBranch,
    newChat,
    deleteChat,
    renameChat,
    clearTypingTitle,
    markDraftSent,
    markEventDeleted,
    markMemoryUndone,
    selectChat,
    attachments,
    uploading,
    attachError,
    addAttachments,
    removeAttachment,
    webpage,
    hasWebpage,
    canvasRevision,
    canvasOpen,
    canvasNotice,
    clearCanvasNotice,
    canvasBusy,
    activateVersion,
    saveFile,
    openCanvas,
    closeCanvas,
    toggleCanvas,
  } = chat

  return (
    <div className={`app${collapsed ? ' is-collapsed' : ''}${canvasOpen ? ' canvas-open' : ''}`}>
      <Sky theme={theme} />

      {isMobile && !collapsed && (
        <div
          className="sidebar-scrim"
          aria-hidden="true"
          onClick={() => setCollapsed(true)}
        />
      )}

      <Sidebar
        chats={chats}
        activeChatId={activeChatId}
        onSelect={handleSelectChat}
        onNewChat={handleNewChat}
        onDelete={deleteChat}
        onRename={renameChat}
        query={query}
        onQuery={setQuery}
        searchInputRef={searchInputRef}
        collapsed={collapsed}
        typingTitleId={typingTitleId}
        onTitleTyped={clearTypingTitle}
        user={user}
        onLogout={handleLogout}
        onSettings={() => {
          setSettingsOpen(true)
          if (isMobile) setCollapsed(true)
        }}
        onClose={() => setCollapsed(true)}
      />
      <SettingsDialog
        user={user}
        open={settingsOpen}
        initialSection={settingsSection}
        onClose={() => {
          setSettingsOpen(false)
          setSettingsSection('')
          setGmailNotice('')
          setCalendarNotice('')
        }}
        onUserUpdated={setUser}
        gmailNotice={gmailNotice}
        onGmailNoticeClear={() => setGmailNotice('')}
        calendarNotice={calendarNotice}
        onCalendarNoticeClear={() => setCalendarNotice('')}
      />

      <main className="main">
        <header className="top-bar">
          {/* DOM order is title-first: it is the view's heading, it matches the
              mobile two-row reading order, and CSS places it mid-row on desktop. */}
          <div className="top-bar-title">
            <TopBarTitle
              title={activeChat.pending ? '' : activeChat.title === 'New chat' ? t('sidebar.newChat') : activeChat.title}
              typing={activeChat.id === typingTitleId}
              onTyped={clearTypingTitle}
              onRename={(title) => renameChat(activeChat.id, title)}
            />
          </div>
          <SidebarControls
            collapsed={collapsed}
            onToggle={toggleSidebar}
            onSearch={openSidebarAndSearch}
            onNewChat={handleNewChat}
            mobile={isMobile}
          />
          <div className="top-bar-right">
            {hasWebpage && (
              <button
                type="button"
                className={`ctrl-btn has-tip${canvasOpen ? ' is-on' : ''}`}
                data-tip={canvasOpen ? t('app.hideWebpage') : t('app.showWebpage')}
                aria-label={canvasOpen ? t('app.hideWebpage') : t('app.showWebpage')}
                aria-pressed={canvasOpen}
                onClick={toggleCanvas}
              >
                <WindowIcon />
              </button>
            )}
            <SpeakerToggle enabled={voice} onChange={setVoice} />
            <ThemeToggle theme={theme} onChange={setTheme} />
            <span className="ai-badge">
              <SparkleIcon aria-hidden="true" />
              <span className="ai-badge-full">{t('app.aiBadge')}</span>
              <span className="ai-badge-short" aria-hidden="true">
                {t('app.aiBadgeShort')}
              </span>
            </span>
          </div>
        </header>

        <ChatWindow
          chat={activeChat}
          user={user}
          isStreaming={isStreaming}
          draft={draft}
          onDraft={setDraft}
          onSend={sendMessage}
          onRetry={retryMessage}
          onEdit={editMessage}
          onRegenerate={regenerateMessage}
          onNavigateBranch={navigateBranch}
          onDraftSent={(draftId) => markDraftSent(activeChatId, draftId)}
          onEventDeleted={(eventId) => markEventDeleted(activeChatId, eventId)}
          onMemoryUndone={(toolName, memoryId) => markMemoryUndone(activeChatId, toolName, memoryId)}
          onManageMemory={() => {
            setSettingsSection('memory')
            setSettingsOpen(true)
          }}
          onOpenWebpage={openCanvas}
          inputRef={inputRef}
          effort={effort}
          onEffort={setEffort}
          mode={activeChat.mode ?? 'chat'}
          onMode={(mode, subject) => setMode(activeChatId, mode, subject)}
          attachments={attachments}
          uploading={uploading}
          attachError={attachError}
          onAttach={addAttachments}
          onRemoveAttachment={removeAttachment}
        />
      </main>

      {canvasOpen && (
        <WebpageCanvas
          chatId={webpage.chatId}
          files={webpage.files}
          previewUrl={webpage.previewUrl}
          previewBase={webpage.previewBase}
          activeVersionId={webpage.activeVersionId ?? null}
          versions={webpage.versions ?? []}
          hasBuild={Boolean(webpage.hasBuild)}
          lastBuild={webpage.lastBuild ?? null}
          streaming={isStreaming}
          busy={canvasBusy}
          notice={canvasNotice}
          revision={canvasRevision}
          onDismissNotice={clearCanvasNotice}
          onActivateVersion={(versionId) => activateVersion(webpage.chatId, versionId)}
          onSaveFile={(path, content, baseVersionId) =>
            saveFile(webpage.chatId, path, content, baseVersionId)
          }
          onAskFix={(text) => {
            setDraft(text)
            inputRef.current?.focus()
          }}
          onClose={closeCanvas}
        />
      )}

      <LanguageGate />
    </div>
  )
}
