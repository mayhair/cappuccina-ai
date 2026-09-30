import { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { SidebarControls } from './components/SidebarControls'
import { TopBarTitle } from './components/TopBarTitle'
import { ChatWindow } from './components/ChatWindow'
import { ThemeToggle } from './components/ThemeToggle'
import { SpeakerToggle } from './components/SpeakerToggle'
import { SparkleIcon } from './components/Icons'
import { Sky, LandingPage } from './components/LandingPage'
import { AlphaGate } from './components/AlphaGate'
import { SettingsDialog } from './components/SettingsDialog'
import { getMe, logout } from './api/authClient'
import { useChatController } from './hooks/useChatController'
import tts from './lib/tts'

export default function App() {
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

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('cappuccina-sidebar-collapsed') === '1',
  )

  useEffect(() => {
    localStorage.setItem('cappuccina-sidebar-collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  const searchInputRef = useRef(null)

  const toggleSidebar = () => setCollapsed((c) => !c)

  const openSidebarAndSearch = () => {
    setCollapsed(false)
    requestAnimationFrame(() => searchInputRef.current?.focus())
  }

  const [user, setUser] = useState(null)
  const [authPhase, setAuthPhase] = useState('checking') // checking | landing
  const [authError, setAuthError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
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
    let cancelled = false
    getMe()
      .then((me) => {
        if (!cancelled) setUser(me)
      })
      .catch(() => {
        if (!cancelled) setAuthPhase('landing')
      })
    return () => {
      cancelled = true
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
    selectChat,
    attachments,
    uploading,
    attachError,
    addAttachments,
    removeAttachment,
  } = chat

  return (
    <div className={`app${collapsed ? ' is-collapsed' : ''}`}>
      <Sky theme={theme} />

      <Sidebar
        chats={chats}
        activeChatId={activeChatId}
        onSelect={selectChat}
        onNewChat={newChat}
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
        onSettings={() => setSettingsOpen(true)}
      />
      <SettingsDialog
        user={user}
        open={settingsOpen}
        onClose={() => {
          setSettingsOpen(false)
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
          <div className="top-bar-left">
            <SidebarControls
              collapsed={collapsed}
              onToggle={toggleSidebar}
              onSearch={openSidebarAndSearch}
              onNewChat={newChat}
            />
            <TopBarTitle
              title={activeChat.pending ? '' : activeChat.title}
              typing={activeChat.id === typingTitleId}
              onTyped={clearTypingTitle}
              onRename={(title) => renameChat(activeChat.id, title)}
            />
          </div>
          <div className="top-bar-right">
            <SpeakerToggle enabled={voice} onChange={setVoice} />
            <ThemeToggle theme={theme} onChange={setTheme} />
            <span className="ai-badge">
              <SparkleIcon />
              I'm an AI
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
    </div>
  )
}
