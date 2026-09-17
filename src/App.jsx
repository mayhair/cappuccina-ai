import { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { SidebarControls } from './components/SidebarControls'
import { ChatWindow } from './components/ChatWindow'
import { ThemeToggle } from './components/ThemeToggle'
import { SparkleIcon } from './components/Icons'
import { Sky, LandingPage } from './components/LandingPage'
import { AuthView } from './components/AuthView'
import { SettingsDialog } from './components/SettingsDialog'
import { getMe, logout } from './api/authClient'
import { useChatController } from './hooks/useChatController'

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
  const [authPhase, setAuthPhase] = useState('checking') // checking | landing | auth
  const [authMode, setAuthMode] = useState('signup')
  const [settingsOpen, setSettingsOpen] = useState(false)

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

  const chat = useChatController(Boolean(user), user?.id)

  const handleSignedIn = (me) => {
    setUser(me)
    setAuthPhase('chat')
  }

  const handleLogout = async () => {
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
        {authPhase === 'auth' ? (
          <AuthView
            mode={authMode}
            theme={theme}
            onTheme={setTheme}
            onSignedIn={handleSignedIn}
          />
        ) : (
          <LandingPage
            theme={theme}
            onTheme={setTheme}
            onStart={() => {
              setAuthMode('signup')
              setAuthPhase('auth')
            }}
            onLogin={() => {
              setAuthMode('login')
              setAuthPhase('auth')
            }}
          />
        )}
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
    newChat,
    deleteChat,
    renameChat,
    clearTypingTitle,
    selectChat,
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
        onClose={() => setSettingsOpen(false)}
        onUserUpdated={setUser}
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
          </div>
          <div className="top-bar-right">
            <ThemeToggle theme={theme} onChange={setTheme} />
            <span className="ai-badge">
              <SparkleIcon />
              I'm an AI
            </span>
          </div>
        </header>

        <ChatWindow
          chat={activeChat}
          isStreaming={isStreaming}
          draft={draft}
          onDraft={setDraft}
          onSend={sendMessage}
          onRetry={retryMessage}
          inputRef={inputRef}
          effort={effort}
          onEffort={setEffort}
          mode={activeChat.mode ?? 'chat'}
          subject={activeChat.subject}
          onMode={(mode, subject) => setMode(activeChatId, mode, subject)}
        />
      </main>
    </div>
  )
}
